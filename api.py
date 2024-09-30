import binascii
import hashlib
import os
import pytz
from datetime import datetime, timedelta
from typing import Optional, List
from azure.communication.email import EmailClient
import redis
from psycopg2 import sql
import psycopg2
import requests
from devices import HomgarHome, MODEL_CODE_MAPPING, HomgarHubDevice, TemperatureAirSensor
from logutil import TRACE, get_logger, logging

logger = get_logger(__file__)
timezone = pytz.timezone('Europe/Paris')

class HomgarApiException(Exception):
    def __init__(self, code, msg):
        super().__init__()
        self.code = code
        self.msg = msg
        logger.error(f"HomgarApiException: code={code}, msg={msg}")

    def __str__(self):
        s = f"HomGar API returned code {self.code}"
        if self.msg:
            s += f" ('{self.msg}')"
        return s


class HomgarApi:
    def __init__(
            self,
            config: Optional[dict] = None,
            api_base_url: str = "https://region3.homgarus.com",
            requests_session: requests.Session = None
    ):
        """
        Initialize the Homgar API object for interacting with the API.
        :param config: Optional dictionary for configuration settings.
        :param api_base_url: The base URL for the Homgar API.
        :param requests_session: Optional requests session to use.
        """
        self.session = requests_session or requests.Session()
        self.base = api_base_url
        self.config = config
        self.redis = redis.Redis(
            host=self.config['redis']['host'], 
            port=6380, 
            password=self.config['redis']['acces-key'], 
            ssl=True
        )
        logger.info("Initialized HomgarApi with base URL: %s", self.base)
        # Initialisation de la connexion PostgreSQL
        self.conn = psycopg2.connect(
            host=self.config['postgres']['host'],
            port=self.config['postgres']['port'],
            dbname='homegar',
            user='homegarapi',
            password=self.config['postgres']['password']
        )
        self.cursor = self.conn.cursor()
        logger.info("Initialized HomgarApi with base URL: %s", self.base)

    def _request(self, method, url, with_auth=True, headers=None, **kwargs):
        """
        Make a HTTP request and log the details.
        :param method: HTTP method (GET, POST, etc.)
        :param url: The URL to request.
        :param with_auth: Boolean to include auth token in headers.
        :param headers: Optional additional headers.
        """
        logger.log(TRACE, "%s %s %s", method, url, kwargs)
        headers = {"lang": "en", "appCode": "1", **(headers or {})}
        if with_auth:
            headers["auth"] = self.get_cache("token")
        response = self.session.request(method, url, headers=headers, **kwargs)
        logger.log(TRACE, "-[%03d]-> %s", response.status_code, response.text)
        return response

    def _request_json(self, method, path, **kwargs):
        """
        Make a HTTP request expecting a JSON response and log the outcome.
        :param method: HTTP method (GET, POST, etc.)
        :param path: The API path to request.
        """
        response = self._request(method, self.base + path, **kwargs).json()
        code = response.get('code')
        if code != 0:
            logger.error("API returned error code %d with message: %s", code, response.get('msg'))
            raise HomgarApiException(code, response.get('msg'))
        return response.get('data')

    def _get_json(self, path, **kwargs):
        """
        Perform a GET request expecting a JSON response.
        :param path: The API path to request.
        """
        logger.info("GET request for path: %s", path)
        return self._request_json("GET", path, **kwargs)

    def _post_json(self, path, body, **kwargs):
        """
        Perform a POST request expecting a JSON response.
        :param path: The API path to request.
        :param body: The JSON body to send in the POST request.
        """
        logger.info("POST request for path: %s with body: %s", path, body)
        return self._request_json("POST", path, json=body, **kwargs)

    def login(self, email: str, password: str, area_code="31") -> None:
        """
        Perform a new login and cache the authentication tokens.
        :param email: Account e-mail.
        :param password: Account password.
        :param area_code: Phone country code associated with the account.
        """
        logger.info("Attempting to login with email: %s", email)
        data = self._post_json("/auth/basic/app/login", {
            "areaCode": area_code,
            "phoneOrEmail": email,
            "password": hashlib.md5(password.encode('utf-8')).hexdigest(),
            "deviceId": binascii.b2a_hex(os.urandom(16)).decode('utf-8')
        }, with_auth=False)
        self.set_cache('email', email)
        self.set_cache('token', data.get('token'))
        self.set_cache('token_expires', datetime.utcnow().timestamp() + data.get('tokenExpired'))
        self.set_cache('refresh_token', data.get('refreshToken'))
        logger.info("Login successful, token cached")

    def get_homes(self) -> List[HomgarHome]:
        """
        Retrieves all HomgarHome objects associated with the logged in account.
        Requires prior login.
        :return: List of HomgarHome objects.
        """
        logger.info("Fetching list of homes")
        data = self._get_json("/app/member/appHome/list")
        homes = [HomgarHome(hid=h.get('hid'), name=h.get('homeName')) for h in data]
        logger.info("Retrieved %d homes", len(homes))
        return homes

    def get_devices_for_hid(self, hid: str) -> List[HomgarHubDevice]:
        """
        Retrieves a device tree associated with the home identified by the given hid (home ID).
        :param hid: The home ID to retrieve hubs and associated subdevices for.
        :return: List of hubs with associated subdevices.
        """
        logger.info("Fetching devices for home ID: %s", hid)
        data = self._get_json("/app/device/getDeviceByHid", params={"hid": str(hid)})
        hubs = []

        def device_base_props(dev_data):
            return dict(
                model=dev_data.get('model'),
                model_code=dev_data.get('modelCode'),
                name=dev_data.get('name'),
                did=dev_data.get('did'),
                mid=dev_data.get('mid'),
                address=dev_data.get('addr'),
                port_number=dev_data.get('portNumber'),
                alerts=dev_data.get('alerts'),
            )

        def get_device_class(dev_data):
            model_code = dev_data.get('modelCode')
            if model_code not in MODEL_CODE_MAPPING:
                logger.warning("Unknown device '%s' with modelCode %d", dev_data.get('model'), model_code)
                return None
            return MODEL_CODE_MAPPING[model_code]

        for hub_data in data:
            subdevices = []
            for subdevice_data in hub_data.get('subDevices', []):
                did = subdevice_data.get('did')
                if did == 1:
                    # Skip hub itself
                    continue
                subdevice_class = get_device_class(subdevice_data)
                if subdevice_class is None:
                    continue
                subdevices.append(subdevice_class(**device_base_props(subdevice_data)))

            hub_class = get_device_class(hub_data)
            if hub_class is None:
                hub_class = HomgarHubDevice

            hubs.append(hub_class(
                **device_base_props(hub_data),
                subdevices=subdevices
            ))

        logger.info("Retrieved %d hubs for home ID: %s", len(hubs), hid)
        return hubs

    def get_device_status(self, hub: HomgarHubDevice) -> None:
        """
        Updates the device status of all subdevices associated with the given hub device.
        :param hub: The hub to update.
        """
        logger.info("Fetching device status for hub ID: %s", hub.mid)
        data = self._get_json("/app/device/getDeviceStatus", params={"mid": str(hub.mid)})
        id_map = {status_id: device for device in [hub, *hub.subdevices] for status_id in device.get_device_status_ids()}

        for subdevice_status in data['subDeviceStatus']:
            device = id_map.get(subdevice_status['id'])
            if device is not None:
                device.set_device_status(subdevice_status)
        logger.info("Device status updated for hub ID: %s", hub.mid)

    def ensure_logged_in(self, email: str, password: str, area_code: str = "31") -> None:
        """
        Ensures this API object has valid credentials.
        If invalid, attempts to login.
        :param email: Account e-mail.
        :param password: Account password.
        :param area_code: Phone country code associated with the account.
        """
        logger.info("Ensuring login status for email: %s", email)
        if (
                self.get_cache('email') != email or
                datetime.fromtimestamp(float(self.get_cache('token_expires'))) - datetime.utcnow() < timedelta(minutes=60)
        ):
            logger.info("Token expired or email mismatch, logging in again")
            self.login(email, password, area_code=area_code)
        else:
            logger.info("Already logged in with valid credentials")

    def init_sensor(self, subdevice: TemperatureAirSensor):
        """
        Initialize the sensor by loading its settings from the database.
        """
        select_query = """
        SELECT alert_temp_max, alert_frequency, alert_enabled, alert_next_check
        FROM device
        WHERE did = %s;
        """
        self.cursor.execute(select_query, (subdevice.did,))
        result = self.cursor.fetchone()
        if result:
            (
                subdevice.alert_temp_max,
                subdevice.alert_frequency,
                subdevice.alert_enabled,
                subdevice.alert_next_check
            ) = result
            logger.info("Loaded sensor settings from database for device ID: %s", subdevice.did)
        else:
            # Si le capteur n'existe pas encore dans la base, l'insérer avec les valeurs par défaut
            insert_query = """
            INSERT INTO device (did, addr, name, model, modelcode, alert_temp_max, alert_frequency, alert_enabled, alert_next_check)
            VALUES (%s, %s, %s, %s,%s, %s, %s, %s, %s);
            """
            self.cursor.execute(insert_query, (
                subdevice.did,
                subdevice.address,
                subdevice.name,
                subdevice.model,
                subdevice.model_code,
                subdevice.alert_temp_max,
                subdevice.alert_frequency,
                subdevice.alert_enabled,
                subdevice.alert_next_check
            ))
            self.conn.commit()
            logger.info("Inserted new sensor into database with default settings for device ID: %s", subdevice.did)

                
    def save_sensor(self, subdevice: TemperatureAirSensor):
        # Itérer sur tous les attributs de l'objet
        for attr_name, attr_value in subdevice.__dict__.items():
            # Filtrer uniquement les attributs qui commencent par 'alert_'
            if attr_name.startswith('alert_'):
                # Construire la clé Redis en ajoutant l'ID devant le nom de la clé
                redis_key = f"{subdevice.did}_{attr_name}"
                # Sauvegarder dans Redis
                if attr_value is not None:
                    self.set_cache(redis_key, attr_value)
                else:
                    self.set_cache(redis_key, "")

    
    def is_max_temperature(self, config, subdevice: TemperatureAirSensor) -> None:
        """
        Checks if the current temperature exceeds the maximum and sends an alert if necessary.
        :param config: Configuration settings.
        :param subdevice: The temperature sensor device to check.
        :param max_temp: Maximum temperature threshold.
        """
        current_time_in_fra = datetime.now(timezone)
        
        subdevice.alert_last_check = current_time_in_fra.strftime('%Y-%m-%d %H:%M:%S')
        subdevice.alert_temp_curr = subdevice.temp_mk_current * 1e-3 - 273.15
        
        # Enregistrer le relevé de température dans la table 'history'
        alert_triggered = False
        alert_message = None
        
        if subdevice.alert_enabled != True:  # Vérifier si la valeur de 'alert_*_enabled' est 1
            logger.info(f"+ Alerts are disabled for device {subdevice.name}. Skipping temperature check.")
            return
        
        curr_temp = subdevice.temp_mk_current * 1e-3 - 273.15
        alert_temp_max = int(subdevice.alert_temp_max)
        logger.info("Checking max temperature for device %s, current: %.2f, max allowed: %s", subdevice.name, subdevice.alert_temp_curr, subdevice.alert_temp_max)
        
        if curr_temp >= alert_temp_max:
            alert_triggered = True
            body = f"ALERT! 🥵 The temperature of sensor \"{self.remove_last_space(subdevice.name)}\" is {round(subdevice.alert_temp_curr, 1)}° with a limit of {subdevice.alert_temp_max}°."
            logger.warning(f"    + {body}")

            if subdevice.alert_next_check is None or current_time_in_fra > timezone.localize(datetime.strptime(subdevice.alert_next_check, '%Y-%m-%d %H:%M:%S')):
                              
                time_next = current_time_in_fra + timedelta(hours=int(subdevice.alert_frequency))
                self.set_cache(f"{subdevice.did}_alert_next_check", time_next.strftime('%Y-%m-%d %H:%M:%S'))
                
                update_query = """
                UPDATE device
                SET alert_next_check = %s
                WHERE did = %s;
                """
                self.cursor.execute(update_query, (
                    time_next,
                    subdevice.did
                ))
                self.conn.commit()
                logger.info("Saved sensor settings to database for device ID: %s", subdevice.did)

                formatted_time_next_alert = time_next.strftime("%d/%m %H:%M")
                with open('template_email.html', 'r', encoding='utf-8') as file:
                    html_template = file.read()
                
                html_content = html_template.replace('[username]', "Franz")
                html_content = html_content.replace('[sensor_name]', self.remove_last_space(subdevice.name))
                html_content = html_content.replace('[curr_temp]', str(round(subdevice.alert_temp_curr, 1)))
                html_content = html_content.replace('[max_temp]', str(subdevice.alert_temp_max))
                html_content = html_content.replace('[time_next]', formatted_time_next_alert)
                
                #self.send_mail(config, html_content)
                logger.info("Temperature alert sent for device: %s", subdevice.name)
                line = f"No further alerts for this sensor until {formatted_time_next_alert}."
                logger.info(line)
            else:
                formatted_time_next_alert = datetime.strptime(subdevice.alert_next_check, '%Y-%m-%d %H:%M:%S').strftime("%d/%m %H:%M")
                logger.info("No alert sent; next alert possible after %s", formatted_time_next_alert)
        
        # Enregistrer le relevé dans la table 'history'
        insert_query = """
        INSERT INTO history (did, timestamp, temperature, alert_triggered, alert_temp_max, alert_message)
        VALUES (%s, %s, %s, %s, %s, %s);
        """
        self.cursor.execute(insert_query, (
            subdevice.did,
            current_time_in_fra,
            subdevice.alert_temp_curr,
            alert_triggered,
            alert_temp_max,
            alert_message
        ))
        self.conn.commit()
        logger.info("Temperature reading recorded in history for device ID: %s", subdevice.did)          
    def remove_last_space(self, s) -> str:
        """
        Removes the trailing space from a string.
        :param s: The string to process.
        :return: String without trailing space.
        """
        logger.debug("Removing trailing space from string: '%s'", s)
        return s.rstrip(' ')
    
    def send_mail(self, config, body_message: str) -> None:
        """
        Sends an email alert to multiple recipients.
        :param config: Configuration settings.
        :param body_message: The body of the email.
        """
        # Récupérer les destinataires depuis le fichier de configuration
        to_receivers = config['notification']

        logger.info("Sending email to %s", ", ".join([recipient['to'] for recipient in to_receivers]))

        connection_string = config['azure-mail']['connection-string']
        client = EmailClient.from_connection_string(connection_string)

        # Créer une liste de destinataires à partir du fichier de configuration
        recipients = [{"address": recipient['to'], "displayName": recipient['displayName']} for recipient in to_receivers]

        message = {
        "content": {
            "subject": "Temperature Alert!",
            "plainText": "",
            "html": body_message
        },
        "recipients": {
            "to": recipients
        },
        "senderAddress": config['azure-mail']['senderAddress']
        }

        logging.getLogger().setLevel(logging.WARNING)  # Temporarily set log level to WARNING
        poller = client.begin_send(message)
        result = poller.result()
        logging.getLogger().setLevel(logging.INFO)  # Restore previous log level

        logger.info("Email sent to %s with result: %s", ", ".join([recipient['to'] for recipient in to_receivers]), result)

    def set_cache(self, key, value, expire_seconds: Optional[int] = None) -> None:
        """
        Stores a value in Redis with an optional expiration.
        :param key: The cache key.
        :param value: The value to store.
        :param expire_seconds: Optional expiration time in seconds.
        """
        if expire_seconds:
            self.redis.setex(key, expire_seconds, value)
            logger.debug("Set cache with expiration: key=%s, value=%s, expire_seconds=%d", key, value, expire_seconds)
        else:
            self.redis.set(key, value)
            logger.debug("Set cache: key=%s, value=%s", key, value)

    def get_cache(self, key):
        """
        Retrieves a value from Redis.
        :param key: The cache key.
        :return: The cached value or None if not found.
        """
        value = self.redis.get(key)
        if value is not None:
            logger.debug("Cache hit for key: %s", key)
            return value.decode('utf-8')
        logger.debug("Cache miss for key: %s", key)
        return None

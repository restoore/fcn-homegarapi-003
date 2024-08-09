import binascii
import hashlib
import os
import pytz
from datetime import datetime, timedelta
from typing import Optional, List
from azure.communication.email import EmailClient
import redis

import requests

from devices import HomgarHome, MODEL_CODE_MAPPING, HomgarHubDevice, TemperatureAirSensor
from logutil import TRACE, get_logger

logger = get_logger(__file__)


class HomgarApiException(Exception):
    def __init__(self, code, msg):
        super().__init__()
        self.code = code
        self.msg = msg

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
        Create an object for interacting with the Homgar API
        :param auth_cache: A dictionary in which authentication information will be stored.
            Save this dict on exit and supply it again next time constructing this object to avoid logging in
            if a valid token is still present.
        :param api_base_url: The base URL for the Homgar API. Omit trailing slash.
        :param requests_session: Optional requests lib session to use. New session is created if omitted.
        """
        self.session = requests_session or requests.Session()
        self.base = api_base_url
        self.config = config
        self.redis = self.redis = redis.Redis(
            host=self.config['redis']['host'], 
            port=6380, 
            password=self.config['redis']['acces_key'], 
            ssl=True
        )

    def _request(self, method, url, with_auth=True, headers=None, **kwargs):
        logger.log(TRACE, "%s %s %s", method, url, kwargs)
        headers = {"lang": "en", "appCode": "1", **(headers or {})}
        if with_auth:
            headers["auth"] = self.get_cache("token")
        response = self.session.request(method, url, headers=headers, **kwargs)
        logger.log(TRACE, "-[%03d]-> %s", response.status_code, response.text)
        return response

    def _request_json(self, method, path, **kwargs):
        response = self._request(method, self.base + path, **kwargs).json()
        code = response.get('code')
        if code != 0:
            raise HomgarApiException(code, response.get('msg'))
        return response.get('data')

    def _get_json(self, path, **kwargs):
        return self._request_json("GET", path, **kwargs)

    def _post_json(self, path, body, **kwargs):
        return self._request_json("POST", path, json=body, **kwargs)

    def login(self, email: str, password: str, area_code="31") -> None:
        """
        Perform a new login.
        :param email: Account e-mail
        :param password: Account password
        :param area_code: Seems to need to be the phone country code associated with the account, e.g. "31" for NL
        """
        data = self._post_json("/auth/basic/app/login", {
            "areaCode": area_code,
            "phoneOrEmail": email,
            "password": hashlib.md5(password.encode('utf-8')).hexdigest(),
            "deviceId": binascii.b2a_hex(os.urandom(16)).decode('utf-8')
        }, with_auth=False)
        self.set_cache('email',email)
        self.set_cache('token',data.get('token'))
        self.set_cache('token_expires',datetime.utcnow().timestamp() + data.get('tokenExpired'))
        self.set_cache('refresh_token',data.get('refreshToken'))

    def get_homes(self) -> List[HomgarHome]:
        """
        Retrieves all HomgarHome objects associated with the logged in account.
        Requires first logging in.
        :return: List of HomgarHome objects
        """
        data = self._get_json("/app/member/appHome/list")
        return [HomgarHome(hid=h.get('hid'), name=h.get('homeName')) for h in data]

    def get_devices_for_hid(self, hid: str) -> List[HomgarHubDevice]:
        """
        Retrieves a device tree associated with the home identified by the given hid (home ID).
        This function returns a list of hubs associated with the home. Each hub contains associated
        subdevices that use the hub as gateway.
        :param hid: The home ID to retrieve hubs and associated subdevices for
        :return: List of hubs with associated subdevicse
        """
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
                    # Display hub
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

        return hubs

    def get_device_status(self, hub: HomgarHubDevice) -> None:
        """
        Updates the device status of all subdevices associated with the given hub device.
        :param hub: The hub to update
        """
        data = self._get_json("/app/device/getDeviceStatus", params={"mid": str(hub.mid)})
        id_map = {status_id: device for device in [hub, *hub.subdevices] for status_id in device.get_device_status_ids()}

        for subdevice_status in data['subDeviceStatus']:
            device = id_map.get(subdevice_status['id'])
            if device is not None:
                device.set_device_status(subdevice_status)

    def ensure_logged_in(self, email: str, password: str, area_code: str = "31") -> None:
        """
        Ensures this API object has valid credentials.
        Attempts to verify the token stored in the auth cache. If invalid, attempts to login.
        See login() for parameter info.
        """
        if (
                self.get_cache('email') != email or
                datetime.fromtimestamp(float(self.get_cache('token_expires'))) - datetime.utcnow() < timedelta(minutes=60)
        ):
            self.login(email, password, area_code=area_code)
            
    def is_max_temperature(self, config, subdevice: TemperatureAirSensor, max_temp: int = 34) -> None:
        curr_temp = subdevice.temp_mk_current * 1e-3 - 273.15
        subdevice.set_max_temperature(config)
        subdevice.set_alert_frequency(config)

        if curr_temp >= subdevice.max_temperature:
            body = f"ALERTE ! 🥵 La température du capteur \"{self.remove_last_space(subdevice.name)}\" est à {round(curr_temp, 1)}° pour une limite à {subdevice.max_temperature}°."
            logger.warning(f"    + {body}")

            # Obtenir l'heure actuelle en France
            timezone = pytz.timezone('Europe/Paris')
            current_time_in_fra = datetime.now(timezone)
            
            # Vérifier si l'alerte doit être déclenchée à nouveau
            last_alert_time = self.get_cache(f"alert_{subdevice.did}_time_next")
            if last_alert_time is None or current_time_in_fra > timezone.localize(datetime.strptime(last_alert_time, '%Y-%m-%d %H:%M:%S')):
                # Mise à jour du cache
                self.set_cache(f"alert_{subdevice.did}_time", current_time_in_fra.strftime('%Y-%m-%d %H:%M:%S'))
                self.set_cache(f"alert_{subdevice.did}_curr_temp", curr_temp)
                self.set_cache(f"alert_{subdevice.did}_max_temp", subdevice.max_temperature)
                
                # Calcul du prochain temps d'alerte
                time_next = current_time_in_fra + timedelta(hours=subdevice.alert_frequency)
                self.set_cache(f"alert_{subdevice.did}_time_next", time_next.strftime('%Y-%m-%d %H:%M:%S'))

                # Envoyer l'alerte
                formatted_time_next_alert = time_next.strftime("%d/%m %H:%M")
                line = f"Vous ne recevrez plus d'alerte pour ce capteur avant le {formatted_time_next_alert}."
                
                with open('template_email.html', 'r', encoding='utf-8') as file:
                    html_template = file.read()
                
                html_content = html_template.replace('[username]', "Franz")
                html_content = html_content.replace('[sensor_name]', self.remove_last_space(subdevice.name))
                html_content = html_content.replace('[curr_temp]', str(round(curr_temp, 1)))
                html_content = html_content.replace('[max_temp]', str(subdevice.max_temperature))
                html_content = html_content.replace('[time_next]', formatted_time_next_alert)
                
                self.send_mail(config, "florian.congre@gmail.com", html_content)
            else:
                formatted_time_next_alert = datetime.strptime(last_alert_time, '%Y-%m-%d %H:%M:%S').strftime("%d/%m %H:%M")
                logger.info(f"    + Pas d'envoi d'alerte pour ce capteur avant le {formatted_time_next_alert}")
                   
    def remove_last_space(self,s) -> str:
        return s.rstrip(' ')
    
    def send_mail(self, config, to_receiver: str, body_message: str) -> None:
        connection_string = config['azure-mail']['connection-string']
        client = EmailClient.from_connection_string(connection_string);
        message = {
            "content": {
                "subject": "Alerte de Température!",
                "plainText": "",
                "html": body_message
            },
            "recipients": {
                "to": [
                    {
                        "address": to_receiver,
                        "displayName": "Customer Name"
                    }
                ]
            },
            "senderAddress": config['azure-mail']['sender']
        }

        poller = client.begin_send(message)
        result = poller.result()
    
    def set_cache(self, key, value, expire_seconds: Optional[int] = None) -> None:
        """
        Stocke une valeur dans Redis. La valeur est automatiquement convertie en chaîne JSON.
        Une expiration peut être spécifiée pour supprimer automatiquement l'entrée après un certain temps.
        """
        if expire_seconds:
            self.redis.setex(key, expire_seconds, value)
        else:
            self.redis.set(key, value)

    def get_cache(self, key):
        """
        Récupère une valeur de Redis et la décode à partir du format JSON.
        """
        value = self.redis.get(key)
        if value is not None:
            return value.decode('utf-8')
        return None
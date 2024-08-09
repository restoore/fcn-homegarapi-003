import logging
import yaml

from api import HomgarApi
from logutil import get_logger, TRACE

import azure.functions as func

# Create the FunctionApp instance for Azure Functions
app = func.FunctionApp()
logger = get_logger(__file__)

# Define a scheduled Azure Function that runs every minute
@app.schedule(schedule="*/30 * * * *", arg_name="myTimer", run_on_startup=True, use_monitor=False) 
async def timer_trigger(myTimer: func.TimerRequest) -> None:
    """
    This function is triggered by a timer every minute. It loads a configuration file,
    initializes the HomgarApi, and runs the main processing function. If the timer is past due,
    it logs a message.
    :param myTimer: The TimerRequest object provided by Azure Functions.
    """
    if myTimer.past_due:
        logger.info('The timer is past due!')

    logger.info("Starting the timer-triggered function...")
    
    logger.info("Loading config.yml file...")
    config_file = 'config.yml'

    # Load the configuration from a YAML file
    try:
        with open(config_file, 'rb') as f:
            config = yaml.unsafe_load(f)
        logger.debug("Configuration loaded successfully from %s", config_file)
    except Exception as e:
        logger.error("Failed to load configuration from %s: %s", config_file, str(e))
        return
    
    try:
        # Initialize the HomgarApi with the loaded configuration
        logger.info("Initializing HomgarApi...")
        api = HomgarApi(config)
        logger.debug("HomgarApi initialized successfully")
        
        # Execute the main processing function
        logger.info("Running the main function...")
        run(api, config)
        logger.info('HomgarApi timer trigger function executed successfully.')
    except Exception as e:
        # Log any exceptions that occur during execution
        logger.error(f'An error occurred during execution: {str(e)}')

def run(api: HomgarApi, config):
    """
    The main processing function that ensures the API is logged in,
    retrieves homes and devices, checks their status, and logs the information.
    :param api: The initialized HomgarApi object.
    :param config: The configuration dictionary.
    """
    try:
        # Ensure that the API has valid login credentials
        logger.info("Ensuring API login...")
        api.ensure_logged_in(config['api-homegar']['email'], config['api-homegar']['password'])
        logger.debug("API login ensured")

        # Retrieve and process each home associated with the account
        logger.info("Retrieving homes from the API...")
        homes = api.get_homes()
        logger.info("Retrieved %d homes", len(homes))
        
        for home in homes:
            logger.info(f"Processing home: ({home.hid}) {home.name}")

            # Retrieve and process each hub and its subdevices for the current home
            logger.info(f"Retrieving devices for home ID {home.hid}...")
            hubs = api.get_devices_for_hid(home.hid)
            logger.info(f"Retrieved {len(hubs)} hubs for home ID {home.hid}")

            for hub in hubs:
                logger.info(f"  - Processing hub: {hub}")
                
                # Update the status of the hub and its subdevices
                logger.info(f"  - Updating status for hub ID {hub.mid}...")
                api.get_device_status(hub)
                logger.debug(f"  - Hub status updated for {hub}")

                # Check each subdevice for temperature and log the results
                for subdevice in hub.subdevices:
                    logger.info(f"    + Processing subdevice: {subdevice}")
                    
                    # Check if the current temperature exceeds the maximum and handle it
                    logger.info(f"    + Checking max temperature for subdevice {subdevice.name}...")
                    api.is_max_temperature(config, subdevice)
                    logger.debug(f"    + Max temperature checked for subdevice {subdevice.name}")

    except Exception as e:
        logger.error(f"An error occurred in the run function: {str(e)}")

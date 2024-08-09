import logging
import yaml

from api import HomgarApi
from logutil import get_logger, TRACE

import azure.functions as func

app = func.FunctionApp()
logger = get_logger(__file__)

@app.schedule(schedule="*/30 * * * *", arg_name="myTimer", run_on_startup=False,
              use_monitor=False) 
async def timer_trigger(myTimer: func.TimerRequest) -> None:
    if myTimer.past_due:
        logging.info('The timer is past due!')
        pass
    
    
    logger.info("Loading config.yml file...")
    config_file = 'config.yml'

    with open(config_file, 'rb') as f:
        config = yaml.unsafe_load(f)
    try:
        api = HomgarApi(config)
        run(api, config)
        logging.info('Homegarapi timer trigger function executed.')
    except Exception as e:
        logging.error(f'An error occurred: {str(e)}')
    
def run(api: HomgarApi, config):
    api.ensure_logged_in(config['api']['email'], config['api']['password'])
    for home in api.get_homes():
        logger.info(f"({home.hid}) {home.name}:")

        for hub in api.get_devices_for_hid(home.hid):
            logger.info(f"  - {hub}")
            api.get_device_status(hub)
            for subdevice in hub.subdevices:
                logger.info(f"    + {subdevice}")
                api.is_max_temperature(config, subdevice)
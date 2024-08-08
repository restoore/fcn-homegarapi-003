import logging
import pickle
from pathlib import Path
from platformdirs import user_cache_dir
import smtplib

import yaml

from api import HomgarApi
from logutil import get_logger, TRACE

import azure.functions as func


app = func.FunctionApp()
logger = get_logger(__file__)

cache_file = (Path(user_cache_dir("homgarapi", ensure_exists=True)) / "cache.pickle")

@app.schedule(schedule="* * * * *", arg_name="myTimer", run_on_startup=True,
              use_monitor=False) 
async def timer_trigger(myTimer: func.TimerRequest) -> None:
    if myTimer.past_due:
        logging.info('The timer is past due!')
        pass
    
    
    logger.info("Loading config.yml file...")
    config_file = 'config.yml'
    cache = {}
    try:
        with open(cache_file, 'rb') as f:
            cache = pickle.load(f)
    except OSError as e:
        logger.info("Could not load cache, starting fresh")

    with open(config_file, 'rb') as f:
        config = yaml.unsafe_load(f)

    try:
        api = HomgarApi(cache)
        demo(api, config)
        logging.info('Homegarapi timer trigger function executed.')
    finally:
        with open(cache_file, 'wb') as f:
            pickle.dump(api.cache, f)
            
    
    
def demo(api: HomgarApi, config):
    api.ensure_logged_in(config['api']['email'], config['api']['password'])
    for home in api.get_homes():
        logger.info(f"({home.hid}) {home.name}:")

        for hub in api.get_devices_for_hid(home.hid):
            logger.info(f"  - {hub}")
            api.get_device_status(hub)
            for subdevice in hub.subdevices:
                logger.info(f"    + {subdevice}")
                api.is_max_temperature(config, subdevice)
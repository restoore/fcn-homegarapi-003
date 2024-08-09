# Homgar API Integration

This project provides an integration with the Homgar API, allowing you to monitor and manage devices associated with your Homgar account. The code is designed to run as an Azure Function, triggered by a timer, which periodically checks the status of devices and sends alerts when necessary.

## Table of Contents

- [Overview](#overview)
- [Installation](#installation)
- [Configuration](#configuration)
- [Usage](#usage)
- [Logging](#logging)
- [Contributing](#contributing)
- [License](#license)

## Overview

The integration is structured into two main components:

1. **HomgarApi**: A class that handles the interaction with the Homgar API, including authentication, retrieving device status, and managing temperature alerts.
2. **Azure Function**: An Azure Function app that triggers the API calls at regular intervals, checks the devices' statuses, and logs the results.

### Features

- **Scheduled Execution**: The Azure Function is scheduled to run 30 minutes to ensure timely updates.
- **Temperature Monitoring**: The system checks if the temperature of connected devices exceeds a predefined threshold and sends an email alert if necessary.
- **Logging**: Extensive logging is implemented to track the flow of operations and handle errors effectively.

## Installation

### Prerequisites

- Python 3.8 or higher
- Azure Functions Core Tools (for local development)
- Access to the Azure portal to deploy and manage the function

### Clone the Repository

```bash
git clone https://github.com/restoore/fcn-homegarapi-003.git
cd fcn-homegarapi-003
```

### Install Dependencies

It is recommended to use a virtual environment:

```bash
python -m venv venv
source venv/bin/activate   # On Windows, use `venv\Scripts\activate`
pip install -r requirements.txt
```

## Configuration

The application requires a configuration file named `config.yml` at the root of the project. This file should contain your API credentials, Redis configuration, and email service connection strings.

### Example `config.yml`

Rename `config.sample.yml` to `config.yml`
```yaml
api-homegar:
  email: "your-email@example.com"
  password: "your-password"

redis:
  host: "your-redis-host"
  acces-key: "your-redis-access-key"

azure-mail:
  connection-string: "your-azure-connection-string"
  sender: "sender-email@example.com"

# Param pour les capteurs : température max à surveiller, cadence des alertes en heure
# exemple si alert-frequency est fixé à 24, la notification ne sera envoyée qu'une fois toutes les 24h en cas de dépassement
sensors:
  - name: 'extérieur '
    max-temperature: 40
    alert-frequency: 24
  - name: 'cage '
    max-temperature: 25
    alert-frequency: 2
  - name: 'congélo '
    max-temperature: -10
    alert-frequency: 24
```

## Usage

### Running Locally

To run the Azure Function locally:

```bash
func start
```

This will start the Azure Function runtime and execute the scheduled function every minute.

### Deployment to Azure

To deploy the function to Azure, use the following commands:

```bash
func azure functionapp publish <YourFunctionAppName>
```

Replace `<YourFunctionAppName>` with the name of your Azure Function App.

## Logging

Logging is extensively used throughout the project to monitor execution and diagnose issues. Logs are classified into different levels:

- `INFO`: General information about the execution flow.
- `DEBUG`: Detailed information useful for debugging.
- `WARNING`: Indications of potential problems or unusual conditions.
- `ERROR`: Errors that prevent the operation from proceeding as expected.

By default, logs are configured to output to the console. You can adjust the logging level and handlers in the `logutil.py` file.

## Contributing

Contributions are welcome! Please follow these steps:

1. Fork the repository.
2. Create a new branch (`git checkout -b feature/your-feature`).
3. Make your changes.
4. Commit your changes (`git commit -am 'Add new feature'`).
5. Push to the branch (`git push origin feature/your-feature`).
6. Create a pull request.

Please make sure to update tests as appropriate.

## License

This project is licensed under the MIT License. See the `LICENSE` file for details.
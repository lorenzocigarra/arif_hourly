# ARIF Weather Data Collector

Automatic hourly collection of weather data from ARIF Puglia network (85 stations).

## Features
- Collects all available variables from all sensors
- Automatic retry with exponential backoff
- Duplicate detection
- Detailed error logging
- Runs automatically every hour via GitHub Actions

## Data
- 85 weather stations across Puglia, Italy
- ~50-70 variables per station
- Hourly collection
- Accumulated in `data/arif/arif_hourly_data.csv`

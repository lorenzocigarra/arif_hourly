# setup_arif.py

import requests
import pandas as pd
from pathlib import Path

DATA_DIR = Path("data/arif")
DATA_DIR.mkdir(parents=True, exist_ok=True)

ARIF_BASE = "http://www.agrometeopuglia.it"

print("Descargando catálogo de estaciones...")

# Descargar estaciones
url = f"{ARIF_BASE}/modules/custom/utility/json/stazioni_rete_assoco.json"
response = requests.get(url, timeout=30)
geojson = response.json()

stations = []
for feature in geojson['features']:
    props = feature['properties']
    coords = feature['geometry']['coordinates']
    
    stations.append({
        'station_id': props['cod_stazione'],
        'name': props['nome_stazione'],
        'municipality': props.get('nome_comune', ''),
        'longitude': float(coords[0]),
        'latitude': float(coords[1]),
    })

df = pd.DataFrame(stations)

# Guardar
stations_file = DATA_DIR / "stations.csv"
df.to_csv(stations_file, index=False, encoding='utf-8')

print(f"✓ {len(df)} estaciones guardadas en {stations_file}")
print("\nPrimeras estaciones:")
print(df.head(10))
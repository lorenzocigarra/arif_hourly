# setup_arif.py
import os
import requests
import pandas as pd
from pathlib import Path

DATA_DIR = Path("data/arif")
DATA_DIR.mkdir(parents=True, exist_ok=True)

ARIF_BASE = "http://www.agrometeopuglia.it"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/javascript, */*; q=0.01"
}

def descargar_catalogo_estaciones():
    print("Descargando catálogo oficial de estaciones (GeoJSON)...")
    url = f"{ARIF_BASE}/modules/custom/utility/json/stazioni_rete_assoco.json"
    
    try:
        response = requests.get(url, headers=HEADERS, timeout=30)
        response.raise_for_status()
        geojson = response.json()
    except Exception as e:
        print(f"✗ Error al consultar el servidor de ARIF: {e}")
        return

    stations = []
    features = geojson.get('features', [])
    
    for feature in features:
        props = feature.get('properties', {})
        geom = feature.get('geometry', {})
        coords = geom.get('coordinates', [None, None])
        
        if props.get('cod_stazione') is None:
            continue
            
        stations.append({
            'station_id': props.get('cod_stazione'),
            'name': props.get('nome_stazione', 'Sin nombre'),
            'municipality': props.get('nome_comune', 'N/A'),
            'longitude': float(coords[0]) if coords[0] is not None else None,
            'latitude': float(coords[1]) if coords[1] is not None else None,
        })

    df = pd.DataFrame(stations)

    # ESCRITURA ATÓMICA DE SEGURIDAD
    stations_file = DATA_DIR / "stations.csv"
    tmp_file = stations_file.parent / f"{stations_file.name}.tmp"
    
    df.to_csv(tmp_file, index=False, encoding='utf-8')
    os.replace(tmp_file, stations_file)

    print(f"✓ {len(df)} estaciones guardadas con éxito en {stations_file}")
    print("\nMuestra de las primeras 5 estaciones procesadas:")
    print(df.head(5))

if __name__ == "__main__":
    descargar_catalogo_estaciones()

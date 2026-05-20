import requests
import pandas as pd
from datetime import datetime
from pathlib import Path
import time
import logging
import sys

# ============================================================================
# CONFIGURACIÓN
# ============================================================================

ARIF_BASE_URL = "http://www.agrometeopuglia.it"
ARIF_API_KEY = "ef7ac29ab10f5d7b827291820308920646a9733477648fd3d2bcace396f687b29752ae715adbff1a8fad70df781d06ad"

DATA_DIR = Path("data/arif")
LOGS_DIR = Path("logs")
DATA_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)

SPATIAL_CSV = DATA_DIR / "arif_spatial_timeseries.csv"

# Variables espaciales
VARIABLES = {
    "T": "tair_hourly",
    "H": "rh_hourly",
    "P": "precip_10min_interval",
    "PC": "precip_today_accum",
    "R": "rs_hourly",
    "W": "wind_speed",
    "WDV": "wdir_hourly"
}

# Mapeo dinámico (api_code, COD_GRANDEZZA) -> (variable_name, unit)
MAPPING = {
    ("T", "TC"): ("tair_hourly", "°C"),
    ("H", "UC"): ("rh_hourly", "%"),
    ("P", "PC"): ("precip_10min_interval", "mm"),
    ("PC", "PC"): ("precip_today_accum", "mm"),
    ("R", "RG"): ("rs_hourly", "W/m²"),
    ("W", "VA"): ("ws_10m_hourly", "m/s"),
    ("W", "VB"): ("ws_2m_hourly", "m/s"),
    ("WDV", "DV"): ("wdir_hourly", "°")
}

# ============================================================================
# LOGGING
# ============================================================================

log_file = LOGS_DIR / f"arif_spatial_{datetime.now().strftime('%Y%m%d')}.log"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file, encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger(__name__)

# ============================================================================
# SESIÓN HTTP
# ============================================================================

session = requests.Session()
session.headers.update({
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "es-419,es;q=0.9",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": f"{ARIF_BASE_URL}/osservazioni/mappa-stazioni-meteo",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
})

session.cookies.set('nibirumail_cookie_advice', '1', 
                   domain='www.agrometeopuglia.it', path='/')

# ============================================================================
# DESCARGA DE DATOS
# ============================================================================

def download_variable(api_code: str, fallback_name: str) -> list:
    """Descargar datos de UNA variable para TODAS las estaciones"""
    url = f"{ARIF_BASE_URL}/api/osservazioni/stazioni"
    params = {
        'api': ARIF_API_KEY,
        'variable': api_code
    }
    
    logger.info(f"Descargando {fallback_name:<25} (API: {api_code})...")
    
    try:
        response = session.get(url, params=params, timeout=30)
        
        if response.status_code != 200:
            logger.error(f"  HTTP {response.status_code}")
            return []
        
        data = response.json()
        
        if not isinstance(data, list) or len(data) == 0:
            logger.warning(f"  Vacío")
            return []
        
        logger.info(f"  ✓ {len(data)} registros")
        
        records = []
        
        for item in data:
            # Valor numérico
            raw_val = item.get('VALORE')
            value = None
            if raw_val is not None:
                try:
                    value = float(str(raw_val).strip())
                except:
                    value = str(raw_val).strip()
            
            # Normalizar fecha: DD-MM-YYYY HH:MM:SS -> YYYY-MM-DD HH:MM:SS
            raw_date = item.get('DATA', '').strip()
            clean_date = raw_date
            
            if raw_date and '-' in raw_date[:10]:
                parts = raw_date.split(' ')
                date_part = parts[0]
                time_part = parts[1] if len(parts) > 1 else ""
                
                date_parts = date_part.split('-')
                if len(date_parts) == 3 and len(date_parts[2]) == 4:
                    # DD-MM-YYYY -> YYYY-MM-DD
                    clean_date = f"{date_parts[2]}-{date_parts[1]}-{date_parts[0]}"
                    if time_part:
                        clean_date = f"{clean_date} {time_part}"
            
            # Mapeo dinámico de variable y unidad
            grandezza = item.get('COD_GRANDEZZA', '')
            mapping_key = (api_code, grandezza)
            
            if mapping_key in MAPPING:
                var_name, unit = MAPPING[mapping_key]
            else:
                var_name = fallback_name
                unit = ""
            
            # Crear registro
            record = {
                'station_id': item.get('COD_STAZIONE'),
                'station_name': item.get('NOME_STAZIONE'),
                'latitude': float(item.get('LAT')) if item.get('LAT') else None,
                'longitude': float(item.get('LON')) if item.get('LON') else None,
                'datetime_local': clean_date,
                'variable': var_name,
                'value': value,
                'unit': unit,
                'timestamp_utc_extraction': datetime.utcnow().isoformat()
            }
            
            records.append(record)
        
        return records
    
    except Exception as e:
        logger.error(f"  Error: {e}")
        return []

def collect_all_data():
    """Función principal de recolección"""
    logger.info("="*70)
    logger.info("INICIO RECOLECCIÓN ARIF SPATIAL")
    logger.info("="*70)
    
    all_records = []
    
    for api_code, fallback_name in VARIABLES.items():
        records = download_variable(api_code, fallback_name)
        all_records.extend(records)
        time.sleep(0.5)  # Delay entre variables
    
    if not all_records:
        logger.error("No se obtuvieron datos")
        sys.exit(1)
    
    # Crear DataFrame
    df_new = pd.DataFrame(all_records)
    
    logger.info(f"Nuevos registros: {len(df_new)}")
    
    # Combinar con datos existentes
    if SPATIAL_CSV.exists():
        try:
            df_prev = pd.read_csv(SPATIAL_CSV)
            df_combined = pd.concat([df_prev, df_new], ignore_index=True)
            
            # Eliminar duplicados
            df_combined = df_combined.drop_duplicates(
                subset=['station_id', 'datetime_local', 'variable'],
                keep='last'
            )
            
            logger.info(f"Registros previos: {len(df_prev)}")
            logger.info(f"Después de deduplicar: {len(df_combined)}")
        except Exception as e:
            logger.warning(f"Error leyendo CSV previo: {e}")
            df_combined = df_new
    else:
        df_combined = df_new
    
    # Ordenar y guardar
    df_combined = df_combined.sort_values(
        by=['datetime_local', 'station_id', 'variable']
    )
    
    df_combined.to_csv(SPATIAL_CSV, index=False, encoding='utf-8')
    
    logger.info("="*70)
    logger.info("RECOLECCIÓN COMPLETADA")
    logger.info(f"  Total registros: {len(df_combined)}")
    logger.info(f"  Estaciones únicas: {df_combined['station_id'].nunique()}")
    logger.info(f"  Variables únicas: {df_combined['variable'].nunique()}")
    logger.info(f"  Archivo: {SPATIAL_CSV}")
    logger.info("="*70)

# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    try:
        collect_all_data()
    except Exception as e:
        logger.error(f"Error fatal: {e}", exc_info=True)
        sys.exit(1)

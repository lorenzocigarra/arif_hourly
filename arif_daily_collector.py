# arif_daily_collector.py
import sys
from pathlib import Path
import pandas as pd
import requests
from datetime import datetime, timedelta
import time
import logging
import base64
import json
from bs4 import BeautifulSoup

# ============================================================================
# CONFIGURACIÓN
# ============================================================================

ARIF_BASE_URL = "http://www.agrometeopuglia.it"

DATA_DIR = Path("data/arif")
LOGS_DIR = Path("logs")
DATA_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)

DAILY_CSV = DATA_DIR / "arif_spatial_daily.csv"

# Variables diarias de ayer con cadence=1D (Código_API: Nombre_Estandar, Unidad)
VARIABLES_DIARIAS = {
    "TDL": ("tair_min_daily", "°C"),
    "TDA": ("tair_mean_daily", "°C"),
    "TDH": ("tair_max_daily", "°C"),
    "HDL": ("rh_min_daily", "%"),
    "HDA": ("rh_mean_daily", "%"),
    "HDH": ("rh_max_daily", "%"),
    "P": ("precip_sum_daily", "mm"),  # Lluvia total diaria
    "R": ("rs_sum_daily", "W/m²"),    # Radiación total diaria
    "WDA": ("ws_mean_daily", "m/s"),
    "WDH": ("ws_max_daily", "m/s")
}

# ============================================================================
# LOGGING
# ============================================================================

log_file = LOGS_DIR / f"arif_daily_{datetime.now().strftime('%Y%m%d')}.log"

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
# EXTRACCIÓN ESTÁTICA ULTRA-RÁPIDA (SIN PLAYWRIGHT)
# ============================================================================

def extraer_api_key_estatico(session: requests.Session) -> str:
    """Descarga el HTML base y decodifica la clave de sesión inyectada por el servidor"""
    logger.info("Extrayendo API Key desde metadatos HTML...")
    url_mapa = f"{ARIF_BASE_URL}/osservazioni/mappa-stazioni-meteo"
    
    try:
        response = session.get(url_mapa, timeout=30)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        # Buscar la etiqueta script que contiene las configuraciones de Drupal
        script_tag = soup.find('script', {'data-drupal-selector': 'drupal-settings-json'})
        
        if script_tag and script_tag.string:
            settings = json.loads(script_tag.string)
            b64_key = settings.get('key')
            
            if b64_key:
                # Decodificar el Base64 para obtener la API Key de 96 caracteres
                api_key = base64.b64decode(b64_key).decode('utf-8')
                logger.info("✓ API Key extraída y decodificada exitosamente.")
                return api_key
                
        logger.error("No se localizó la clave dentro de las configuraciones de la página.")
        return None
    except Exception as e:
        logger.error(f"Error extrayendo la clave estática: {e}")
        return None

# ============================================================================
# DESCARGA DE DATOS DIARIOS
# ============================================================================

def download_daily_variable(session: requests.Session, api_key: str, api_code: str, std_name: str, unit: str, fecha_str: str) -> list:
    """Descargar datos de una variable diaria de ayer para todas las estaciones"""
    url = f"{ARIF_BASE_URL}/api/osservazioni/stazioni"
    params = {
        'api': api_key,
        'variable': api_code,
        'cadence': '1D',
        'data': fecha_str
    }
    
    logger.info(f"Descargando variable diaria: {std_name:<20} (API: {api_code})...")
    
    try:
        response = session.get(url, params=params, timeout=30)
        
        if response.status_code != 200:
            logger.error(f"  HTTP {response.status_code}")
            return []
        
        data = response.json()
        
        if not isinstance(data, list) or len(data) == 0:
            logger.warning(f"  Vacío")
            return []
        
        logger.info(f"  ✓ {len(data)} registros obtenidos")
        
        records = []
        
        for item in data:
            raw_val = item.get('VALORE')
            value = None
            if raw_val is not None:
                try:
                    value = float(str(raw_val).strip())
                except:
                    value = str(raw_val).strip()
            
            raw_date = item.get('DATA', '').strip()
            clean_date = raw_date
            
            # Normalizar fecha
            if raw_date and '-' in raw_date[:10]:
                parts = raw_date.split(' ')
                date_part = parts[0]
                date_parts = date_part.split('-')
                if len(date_parts) == 3 and len(date_parts[2]) == 4:
                    clean_date = f"{date_parts[2]}-{date_parts[1]}-{date_parts[0]}"
            
            record = {
                'station_id': item.get('COD_STAZIONE'),
                'station_name': item.get('NOME_STAZIONE'),
                'latitude': float(item.get('LAT')) if item.get('LAT') else None,
                'longitude': float(item.get('LON')) if item.get('LON') else None,
                'date': clean_date,
                'variable': std_name,
                'value': value,
                'unit': unit,
                'timestamp_extraction': datetime.utcnow().isoformat()
            }
            records.append(record)
            
        return records
    except Exception as e:
        logger.error(f"  Error: {e}")
        return []

def collect_daily_data():
    """Función principal de recolección diaria incremental"""
    logger.info("="*70)
    logger.info("INICIO RECOLECCIÓN DIARIA INCREMENTAL (ARIF)")
    logger.info("="*70)
    
    # Calcular fecha de ayer en formato YYYY-MM-DD
    fecha_ayer_obj = datetime.now() - timedelta(days=1)
    fecha_ayer_str = fecha_ayer_obj.strftime('%Y-%m-%d')
    
    # 1. Comprobar si ayer ya fue descargado de manera incremental
    if DAILY_CSV.exists():
        try:
            df_prev = pd.read_csv(DAILY_CSV)
            if 'date' in df_prev.columns and 'variable' in df_prev.columns:
                df_norm = df_prev.copy()
                df_norm['date_parsed'] = pd.to_datetime(df_norm['date'], errors='coerce')
                df_norm = df_norm.dropna(subset=['date_parsed'])
                df_norm['date_std'] = df_norm['date_parsed'].dt.strftime('%Y-%m-%d')
                
                fechas_completas = df_norm.groupby('date_std')['variable'].nunique()
                fechas_descargadas = set(fechas_completas[fechas_completas >= 8].index.astype(str))
                
                if fecha_ayer_str in fechas_descargadas:
                    logger.info(f"✅ Los datos diarios de ayer ({fecha_ayer_str}) ya están completos localmente. Omitiendo descarga.")
                    return
        except Exception as e:
            logger.warning(f"Error procesando CSV previo: {e}")
            
    # 2. Inicializar sesión de solicitudes con cookies
    session = requests.Session()
    session.headers.update({
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "es-419,es;q=0.9",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": f"{ARIF_BASE_URL}/osservazioni/mappa-stazioni-meteo",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    })
    for dom in ["www.agrometeopuglia.it", "agrometeopuglia.it", ".agrometeopuglia.it"]:
        session.cookies.set('nibirumail_cookie_advice', '1', domain=dom, path='/')
    
    # 3. Obtener clave activa estática
    api_key = extraer_api_key_estatico(session)
    if not api_key:
        logger.error("No se pudo obtener una API Key válida. Cancelando ejecución.")
        sys.exit(1)
        
    logger.info(f"API Key activa establecida para descargas directas: {api_key[:15]}...")
    
    # 4. Descargar cada variable para la fecha de ayer
    all_records = []
    for api_code, (std_name, unit) in VARIABLES_DIARIAS.items():
        records = download_daily_variable(session, api_key, api_code, std_name, unit, fecha_ayer_str)
        all_records.extend(records)
        time.sleep(0.5)
        
    if not all_records:
        logger.error("No se descargaron registros.")
        sys.exit(1)
        
    df_new = pd.DataFrame(all_records)
    # Filtrar rigurosamente registros que coincidan con la fecha de ayer
    df_new = df_new[df_new['date'] == fecha_ayer_str]
    
    logger.info(f"Nuevos registros válidos obtenidos de ayer: {len(df_new)}")
    
    # 5. Consolidar con el historial existente
    if DAILY_CSV.exists():
        try:
            df_prev = pd.read_csv(DAILY_CSV)
            df_combined = pd.concat([df_prev, df_new], ignore_index=True)
            df_combined = df_combined.drop_duplicates(
                subset=['station_id', 'date', 'variable'],
                keep='last'
            )
            logger.info(f"Registros previos cargados: {len(df_prev)}")
            logger.info(f"Total registros únicos consolidados: {len(df_combined)}")
        except Exception as e:
            logger.warning(f"Error procesando el histórico: {e}")
            df_combined = df_new
    else:
        df_combined = df_new
        
    df_combined = df_combined.sort_values(by=['date', 'station_id', 'variable'])
    df_combined.to_csv(DAILY_CSV, index=False, encoding='utf-8-sig')
    
    logger.info("="*70)
    logger.info("RECOLECCIÓN DIARIA FINALIZADA CON ÉXITO")
    print(f"  Día procesado: {fecha_ayer_str}")
    print(f"  Total registros acumulados: {len(df_combined)}")
    print(f"  Archivo guardado en: {DAILY_CSV}")
    logger.info("="*70)

# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    try:
        collect_daily_data()
    except Exception as e:
        logger.error(f"Error fatal en la ejecución: {e}", exc_info=True)
        sys.exit(1)

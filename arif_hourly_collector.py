# arif_hourly_collector.py

import sys
from pathlib import Path
import pandas as pd
import requests
from datetime import datetime, timedelta
import time
import json
import logging
from typing import Optional, Dict, List, Tuple

# ============================================================================
# CONFIGURACIÓN
# ============================================================================

ARIF_BASE_URL = "http://www.agrometeopuglia.it"
ARIF_API_KEY = "ef7ac29ab10f5d7b827291820308920646a9733477648fd3d2bcace396f687b2f72d94360072416eda0efcc019743d2e"

DATA_DIR = Path("data/arif")
LOGS_DIR = Path("logs")
DATA_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)

# Archivos
DATA_FILE = DATA_DIR / "arif_hourly_data.csv"
STATIONS_FILE = DATA_DIR / "stations.csv"
ERRORS_FILE = LOGS_DIR / "errors.json"

# Reintentos
MAX_RETRIES = 3
RETRY_DELAY = 5  # segundos
REQUEST_TIMEOUT = 30
INTER_STATION_DELAY = 0.5
INTER_SENSOR_DELAY = 0.2

# Headers
HEADERS = {
    'Accept': 'application/json, text/javascript, */*; q=0.01',
    'Accept-Encoding': 'gzip, deflate',
    'Accept-Language': 'es-419,es;q=0.9',
    'Connection': 'keep-alive',
    'DNT': '1',
    'Host': 'www.agrometeopuglia.it',
    'Referer': f'{ARIF_BASE_URL}/osservazioni/mappa-stazioni-meteo',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'X-Requested-With': 'XMLHttpRequest'
}

# ============================================================================
# LOGGING
# ============================================================================

log_file = LOGS_DIR / f"arif_{datetime.now().strftime('%Y%m%d')}.log"

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

def create_session() -> requests.Session:
    """Crear sesión HTTP con headers"""
    session = requests.Session()
    session.headers.update(HEADERS)
    return session

session = create_session()

# ============================================================================
# FUNCIONES DE RED CON REINTENTOS
# ============================================================================

def request_with_retry(url: str, params: Dict, retries: int = MAX_RETRIES) -> Optional[requests.Response]:
    """Hacer request con reintentos exponenciales"""
    for attempt in range(retries):
        try:
            response = session.get(url, params=params, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            return response
        
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 401:
                logger.error(f"API key inválido o expirado")
                return None
            
            if attempt < retries - 1:
                wait_time = RETRY_DELAY * (2 ** attempt)
                logger.warning(f"HTTP {e.response.status_code}, reintento {attempt+1}/{retries} en {wait_time}s")
                time.sleep(wait_time)
            else:
                logger.error(f"Fallo después de {retries} intentos: {e}")
                return None
        
        except requests.exceptions.RequestException as e:
            if attempt < retries - 1:
                wait_time = RETRY_DELAY * (2 ** attempt)
                logger.warning(f"Error de red, reintento {attempt+1}/{retries} en {wait_time}s: {e}")
                time.sleep(wait_time)
            else:
                logger.error(f"Fallo después de {retries} intentos: {e}")
                return None
    
    return None

# ============================================================================
# OBTENCIÓN DE DATOS
# ============================================================================

def get_station_sensors(station_id: str) -> List[Dict]:
    """Obtener lista de sensores disponibles en una estación"""
    url = f"{ARIF_BASE_URL}/api/osservazioni/sensori/{station_id}"
    params = {'api': ARIF_API_KEY}
    
    response = request_with_retry(url, params)
    
    if response:
        try:
            sensors = response.json()
            return sensors if isinstance(sensors, list) else []
        except:
            logger.error(f"Error parseando sensores de {station_id}")
    
    return []

def get_sensor_data(station_id: str, sensor_code: str) -> Optional[Dict]:
    """Obtener datos de un sensor específico"""
    url = f"{ARIF_BASE_URL}/api/osservazioni/ultimaMisura/{station_id}"
    params = {
        'api': ARIF_API_KEY,
        'sensor': sensor_code,
        'misurazione': 0
    }
    
    response = request_with_retry(url, params)
    
    if response:
        try:
            data = response.json()
            
            if data and isinstance(data, list) and len(data) > 0:
                # Retornar primer elemento con datos
                return data[0]
        except:
            logger.error(f"Error parseando datos de {station_id}/{sensor_code}")
    
    return None

def get_all_station_data(station_id: str, station_name: str) -> Optional[pd.DataFrame]:
    """Obtener datos de todos los sensores de una estación"""
    logger.info(f"Procesando {station_id} - {station_name}")
    
    # 1. Obtener lista de sensores
    sensors = get_station_sensors(station_id)
    
    if not sensors:
        logger.warning(f"  Sin sensores disponibles")
        return None
    
    logger.info(f"  {len(sensors)} sensores encontrados")
    
    # 2. Obtener datos de cada sensor
    all_sensor_data = {}
    successful_sensors = 0
    
    for sensor in sensors:
        sensor_code = sensor['Codice']
        sensor_name = sensor['Sensore']
        
        data = get_sensor_data(station_id, sensor_code)
        
        if data:
            # Agregar todos los valores al diccionario
            for key, value in data.items():
                if key not in ['station_id', 'timestamp_extraction']:
                    # Prefijar con código del sensor para evitar colisiones
                    col_name = f"{sensor_code}_{key}" if key not in ['Data'] else key
                    all_sensor_data[col_name] = value
            
            successful_sensors += 1
        
        time.sleep(INTER_SENSOR_DELAY)
    
    logger.info(f"  ✓ {successful_sensors}/{len(sensors)} sensores con datos")
    
    if all_sensor_data:
        # Agregar metadatos
        all_sensor_data['station_id'] = station_id
        all_sensor_data['station_name'] = station_name
        all_sensor_data['timestamp_utc'] = datetime.utcnow().isoformat()
        all_sensor_data['timestamp_local'] = datetime.now().isoformat()
        
        return pd.DataFrame([all_sensor_data])
    
    return None

# ============================================================================
# GESTIÓN DE ERRORES
# ============================================================================

def load_errors() -> Dict:
    """Cargar registro de errores"""
    if ERRORS_FILE.exists():
        try:
            with open(ERRORS_FILE, 'r') as f:
                return json.load(f)
        except:
            pass
    return {'failed_stations': {}}

def save_errors(errors: Dict):
    """Guardar registro de errores"""
    with open(ERRORS_FILE, 'w') as f:
        json.dump(errors, f, indent=2)

def register_error(station_id: str, error_msg: str, errors: Dict):
    """Registrar error de una estación"""
    if station_id not in errors['failed_stations']:
        errors['failed_stations'][station_id] = []
    
    errors['failed_stations'][station_id].append({
        'timestamp': datetime.now().isoformat(),
        'error': error_msg
    })

# ============================================================================
# CARGA DE ESTACIONES
# ============================================================================

def load_stations() -> pd.DataFrame:
    """Cargar catálogo de estaciones"""
    if not STATIONS_FILE.exists():
        logger.error(f"Archivo de estaciones no encontrado: {STATIONS_FILE}")
        logger.error("Ejecutá primero el script de setup para descargar el catálogo")
        sys.exit(1)
    
    return pd.read_csv(STATIONS_FILE)

# ============================================================================
# VERIFICACIÓN DE DUPLICADOS
# ============================================================================

def check_recent_collection() -> bool:
    """Verificar si ya se recolectó datos en la última hora"""
    if not DATA_FILE.exists():
        return False
    
    try:
        df = pd.read_csv(DATA_FILE)
        
        if 'timestamp_utc' not in df.columns:
            return False
        
        # Obtener timestamp más reciente
        df['ts'] = pd.to_datetime(df['timestamp_utc'])
        last_collection = df['ts'].max()
        
        # Si fue hace menos de 50 minutos, skip
        time_diff = datetime.utcnow() - last_collection.to_pydatetime()
        
        if time_diff < timedelta(minutes=50):
            logger.info(f"Última recolección hace {time_diff.total_seconds()/60:.1f} min - saltando")
            return True
    except:
        pass
    
    return False

# ============================================================================
# RECOLECCIÓN PRINCIPAL
# ============================================================================

def collect_data():
    """Función principal de recolección"""
    logger.info("="*70)
    logger.info("INICIO RECOLECCIÓN ARIF")
    logger.info("="*70)
    
    # Verificar si ya se recolectó recientemente
    if check_recent_collection():
        logger.info("Recolección reciente detectada - finalizando")
        return
    
    # Cargar estaciones
    stations = load_stations()
    logger.info(f"Estaciones a procesar: {len(stations)}")
    
    # Cargar registro de errores
    errors = load_errors()
    
    # Recolectar datos
    all_data = []
    successful = 0
    failed = 0
    start_time = time.time()
    
    for idx, row in stations.iterrows():
        station_id = row['station_id']
        station_name = row['name']
        
        try:
            df = get_all_station_data(station_id, station_name)
            
            if df is not None and not df.empty:
                all_data.append(df)
                successful += 1
            else:
                failed += 1
                register_error(station_id, "Sin datos", errors)
        
        except Exception as e:
            failed += 1
            error_msg = f"{type(e).__name__}: {str(e)}"
            logger.error(f"Error en {station_id}: {error_msg}")
            register_error(station_id, error_msg, errors)
        
        time.sleep(INTER_STATION_DELAY)
        
        # Progreso cada 10 estaciones
        if (idx + 1) % 10 == 0:
            elapsed = (time.time() - start_time) / 60
            logger.info(f"Progreso: {idx+1}/{len(stations)} ({elapsed:.1f} min)")
    
    # Guardar errores
    save_errors(errors)
    
    # Procesar y guardar datos
    if all_data:
        new_data = pd.concat(all_data, ignore_index=True)
        
        # Cargar existentes y combinar
        if DATA_FILE.exists():
            existing = pd.read_csv(DATA_FILE)
            combined = pd.concat([existing, new_data], ignore_index=True)
            
            # Eliminar duplicados por timestamp y estación
            combined = combined.drop_duplicates(
                subset=['station_id', 'timestamp_utc'],
                keep='last'
            )
        else:
            combined = new_data
        
        # Guardar
        combined.to_csv(DATA_FILE, index=False, encoding='utf-8')
        
        elapsed = (time.time() - start_time) / 60
        
        logger.info("="*70)
        logger.info("RECOLECCIÓN COMPLETADA")
        logger.info(f"  Exitosas: {successful}/{len(stations)}")
        logger.info(f"  Fallidas: {failed}/{len(stations)}")
        logger.info(f"  Nuevos registros: {len(new_data)}")
        logger.info(f"  Total acumulado: {len(combined)}")
        logger.info(f"  Variables: {len(new_data.columns)}")
        logger.info(f"  Tiempo: {elapsed:.1f} minutos")
        logger.info("="*70)
    
    else:
        logger.error("No se recolectaron datos de ninguna estación")
        sys.exit(1)

# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    try:
        collect_data()
    except Exception as e:
        logger.error(f"Error fatal: {e}", exc_info=True)
        sys.exit(1)
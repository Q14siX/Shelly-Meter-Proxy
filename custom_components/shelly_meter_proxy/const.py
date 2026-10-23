"""Settings for Shelly Meter Proxy."""
DOMAIN = "shelly_meter_proxy"
CONF_MODE = "mode"
CONF_TOTAL = "total_entity"
CONF_PHASE_A = "phase_a_entity"
CONF_PHASE_B = "phase_b_entity"
CONF_PHASE_C = "phase_c_entity"
CONF_SPLIT = "total_mapping"
CONF_INVERT = "invert_sign"
CONF_TIMEOUT = "stale_seconds"
CONF_SHELLY_IP = "original_shelly_ip"
CONF_HTTP_PORT = "http_port"
CONF_LAN_IP = "lan_ip"
CONF_CLOUD_ENABLE = "cloud_enabled"
CONF_CLOUD_KEY = "cloud_key"
CONF_CLOUD_SERVER = "cloud_server"
CONF_IDENT = "identity"
DEFAULT_TIMEOUT = 45
DEFAULT_HTTP_PORT = 80

CONF_HANDOVER_ACK = "handover_confirmed"

CONF_ORIGINAL_INFO = "original_info"

CONF_TLS_MODE = "cloud_tls_mode"
CONF_TLS_FINGERPRINT = "cloud_tls_fingerprint"
TLS_VERIFY = "verify"
TLS_PIN = "pin"
TLS_COMPAT = "compat"

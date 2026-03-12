$env:L6_CAPTURE_MODE = "true"
$env:L6_CHALLENGES_ENABLED = "true"
$env:L6_CAPTURE_PLAYER_ID = "P1"
$env:L6_CAPTURE_GAME_TITLE = "Warzone"
$env:L6_CHALLENGE_INTERVAL_TICKS = "30"
$env:MQTT_ENABLED = "false"
$env:COAP_ENABLED = "false"
$env:DUALSHOCK_ENABLED = "true"

cd C:\Users\Contr\vapi-pebble-prototype\bridge
python -m vapi_bridge.main

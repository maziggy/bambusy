import json
import ssl
import asyncio
import logging
import time
from collections import deque
from datetime import datetime
from typing import Callable
from dataclasses import dataclass, field

import paho.mqtt.client as mqtt

logger = logging.getLogger(__name__)


@dataclass
class MQTTLogEntry:
    """Log entry for MQTT message debugging."""
    timestamp: str
    topic: str
    direction: str  # "in" or "out"
    payload: dict


@dataclass
class HMSError:
    """Health Management System error from printer."""
    code: str
    module: int
    severity: int  # 1=fatal, 2=serious, 3=common, 4=info
    message: str = ""


@dataclass
class KProfile:
    """Pressure advance (K) calibration profile from printer."""
    slot_id: int
    extruder_id: int
    nozzle_id: str
    nozzle_diameter: str
    filament_id: str
    name: str
    k_value: str
    n_coef: str = "0.000000"
    ams_id: int = 0
    tray_id: int = -1
    setting_id: str | None = None


@dataclass
class PrinterState:
    connected: bool = False
    state: str = "unknown"
    current_print: str | None = None
    subtask_name: str | None = None
    progress: float = 0.0
    remaining_time: int = 0
    layer_num: int = 0
    total_layers: int = 0
    temperatures: dict = field(default_factory=dict)
    raw_data: dict = field(default_factory=dict)
    gcode_file: str | None = None
    subtask_id: str | None = None
    hms_errors: list = field(default_factory=list)  # List of HMSError
    kprofiles: list = field(default_factory=list)  # List of KProfile


class BambuMQTTClient:
    """MQTT client for Bambu Lab printer communication."""

    MQTT_PORT = 8883

    def __init__(
        self,
        ip_address: str,
        serial_number: str,
        access_code: str,
        on_state_change: Callable[[PrinterState], None] | None = None,
        on_print_start: Callable[[dict], None] | None = None,
        on_print_complete: Callable[[dict], None] | None = None,
    ):
        self.ip_address = ip_address
        self.serial_number = serial_number
        self.access_code = access_code
        self.on_state_change = on_state_change
        self.on_print_start = on_print_start
        self.on_print_complete = on_print_complete

        self.state = PrinterState()
        self._client: mqtt.Client | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._previous_gcode_state: str | None = None
        self._previous_gcode_file: str | None = None
        self._was_running: bool = False  # Track if we've seen RUNNING state for current print
        self._completion_triggered: bool = False  # Prevent duplicate completion triggers
        self._message_log: deque[MQTTLogEntry] = deque(maxlen=100)
        self._logging_enabled: bool = False
        self._last_message_time: float = 0.0  # Track when we last received a message

        # K-profile command tracking
        self._sequence_id: int = 0
        self._pending_kprofile_response: asyncio.Event | None = None
        self._kprofile_response_data: list | None = None

    @property
    def topic_subscribe(self) -> str:
        return f"device/{self.serial_number}/report"

    @property
    def topic_publish(self) -> str:
        return f"device/{self.serial_number}/request"

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        if rc == 0:
            self.state.connected = True
            client.subscribe(self.topic_subscribe)
            # Request full status update
            self._request_push_all()
            # Immediately broadcast connection state change
            if self.on_state_change:
                self.on_state_change(self.state)
        else:
            self.state.connected = False

    def _on_disconnect(self, client, userdata, disconnect_flags=None, rc=None, properties=None):
        # Ignore spurious disconnect callbacks if we've received a message recently
        # Paho-mqtt sometimes fires disconnect callbacks while the connection is still active
        time_since_last_message = time.time() - self._last_message_time
        if time_since_last_message < 30.0 and self._last_message_time > 0:
            logger.debug(
                f"[{self.serial_number}] Ignoring spurious disconnect (last message {time_since_last_message:.1f}s ago)"
            )
            return

        logger.warning(f"[{self.serial_number}] MQTT disconnected: rc={rc}, flags={disconnect_flags}")
        self.state.connected = False
        if self.on_state_change:
            self.on_state_change(self.state)

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode())
            # Track last message time - receiving a message proves we're connected
            self._last_message_time = time.time()
            self.state.connected = True
            # Log message if logging is enabled
            if self._logging_enabled:
                self._message_log.append(MQTTLogEntry(
                    timestamp=datetime.now().isoformat(),
                    topic=msg.topic,
                    direction="in",
                    payload=payload,
                ))
            self._process_message(payload)
        except json.JSONDecodeError:
            pass

    def _process_message(self, payload: dict):
        """Process incoming MQTT message from printer."""
        if "print" in payload:
            print_data = payload["print"]
            # Log when we see gcode_state changes
            if "gcode_state" in print_data:
                logger.info(
                    f"[{self.serial_number}] Received gcode_state: {print_data.get('gcode_state')}, "
                    f"gcode_file: {print_data.get('gcode_file')}, subtask_name: {print_data.get('subtask_name')}"
                )

            # Check for K-profile response (extrusion_cali)
            if "command" in print_data and print_data.get("command") == "extrusion_cali_get":
                self._handle_kprofile_response(print_data)

            self._update_state(print_data)

    def _update_state(self, data: dict):
        """Update printer state from message data."""
        previous_state = self.state.state

        # Update state fields
        if "gcode_state" in data:
            self.state.state = data["gcode_state"]
        if "gcode_file" in data:
            self.state.gcode_file = data["gcode_file"]
            self.state.current_print = data["gcode_file"]
        if "subtask_name" in data:
            self.state.subtask_name = data["subtask_name"]
            # Prefer subtask_name as current_print if available
            if data["subtask_name"]:
                self.state.current_print = data["subtask_name"]
        if "subtask_id" in data:
            self.state.subtask_id = data["subtask_id"]
        if "mc_percent" in data:
            self.state.progress = float(data["mc_percent"])
        if "mc_remaining_time" in data:
            self.state.remaining_time = int(data["mc_remaining_time"])
        if "layer_num" in data:
            self.state.layer_num = int(data["layer_num"])
        if "total_layer_num" in data:
            self.state.total_layers = int(data["total_layer_num"])

        # Temperature data
        temps = {}
        # Log all temperature-related fields for debugging (only when we have temp data)
        temp_fields = {k: v for k, v in data.items() if 'temp' in k.lower() or 'nozzle' in k.lower()}
        if temp_fields and not hasattr(self, '_temp_fields_logged'):
            logger.info(f"[{self.serial_number}] Temperature fields in MQTT data: {temp_fields}")
            self._temp_fields_logged = True
        if "bed_temper" in data:
            temps["bed"] = float(data["bed_temper"])
        if "bed_target_temper" in data:
            temps["bed_target"] = float(data["bed_target_temper"])
        if "nozzle_temper" in data:
            temps["nozzle"] = float(data["nozzle_temper"])
        if "nozzle_target_temper" in data:
            temps["nozzle_target"] = float(data["nozzle_target_temper"])
        # Second nozzle for dual-extruder printers (H2 series)
        # Try multiple possible field names used by different firmware versions
        if "nozzle_temper_2" in data:
            temps["nozzle_2"] = float(data["nozzle_temper_2"])
        elif "right_nozzle_temper" in data:
            temps["nozzle_2"] = float(data["right_nozzle_temper"])
        if "nozzle_target_temper_2" in data:
            temps["nozzle_2_target"] = float(data["nozzle_target_temper_2"])
        elif "right_nozzle_target_temper" in data:
            temps["nozzle_2_target"] = float(data["right_nozzle_target_temper"])
        # Also check for left nozzle as primary (some H2 models)
        if "left_nozzle_temper" in data and "nozzle" not in temps:
            temps["nozzle"] = float(data["left_nozzle_temper"])
        if "left_nozzle_target_temper" in data and "nozzle_target" not in temps:
            temps["nozzle_target"] = float(data["left_nozzle_target_temper"])
        if "chamber_temper" in data:
            temps["chamber"] = float(data["chamber_temper"])
        if temps:
            self.state.temperatures = temps

        # Parse HMS (Health Management System) errors
        if "hms" in data:
            hms_list = data["hms"]
            self.state.hms_errors = []
            if isinstance(hms_list, list):
                for hms in hms_list:
                    if isinstance(hms, dict):
                        # HMS format: {"attr": code, "code": full_code}
                        # The code is a hex string, severity is in bits
                        code = hms.get("code", hms.get("attr", "0"))
                        if isinstance(code, int):
                            code = hex(code)
                        # Parse severity from code (typically last 4 bits indicate level)
                        try:
                            code_int = int(str(code).replace("0x", ""), 16) if code else 0
                            severity = (code_int >> 16) & 0xF  # Extract severity bits
                            module = (code_int >> 24) & 0xFF  # Extract module bits
                        except (ValueError, TypeError):
                            severity = 3
                            module = 0
                        self.state.hms_errors.append(HMSError(
                            code=str(code),
                            module=module,
                            severity=severity if severity > 0 else 3,
                        ))

        self.state.raw_data = data

        # Log state transitions for debugging
        if "gcode_state" in data:
            logger.debug(
                f"[{self.serial_number}] gcode_state: {self._previous_gcode_state} -> {self.state.state}, "
                f"file: {self.state.gcode_file}, subtask: {self.state.subtask_name}"
            )

        # Detect print start (state changes TO RUNNING with a file)
        current_file = self.state.gcode_file or self.state.current_print
        is_new_print = (
            self.state.state == "RUNNING"
            and self._previous_gcode_state != "RUNNING"
            and current_file
        )
        # Also detect if file changed while running (new print started)
        is_file_change = (
            self.state.state == "RUNNING"
            and current_file
            and current_file != self._previous_gcode_file
            and self._previous_gcode_file is not None
        )

        # Track RUNNING state for more robust completion detection
        if self.state.state == "RUNNING" and current_file:
            if not self._was_running:
                logger.info(f"[{self.serial_number}] Now tracking RUNNING state for {current_file}")
            self._was_running = True
            self._completion_triggered = False

        if is_new_print or is_file_change:
            # Clear any old HMS errors when a new print starts
            self.state.hms_errors = []
            # Reset completion tracking for new print
            self._was_running = True
            self._completion_triggered = False

        if (is_new_print or is_file_change) and self.on_print_start:
            logger.info(
                f"[{self.serial_number}] PRINT START detected - file: {current_file}, "
                f"subtask: {self.state.subtask_name}, is_new: {is_new_print}, is_file_change: {is_file_change}"
            )
            self.on_print_start({
                "filename": current_file,
                "subtask_name": self.state.subtask_name,
                "raw_data": data,
            })

        # Detect print completion (FINISH = success, FAILED = error, IDLE = aborted)
        # Use _was_running flag in addition to _previous_gcode_state for more robust detection
        # This handles cases where server restarts during a print
        should_trigger_completion = (
            self.state.state in ("FINISH", "FAILED")
            and not self._completion_triggered
            and self.on_print_complete
            and (
                self._previous_gcode_state == "RUNNING"  # Normal transition
                or (self._was_running and self._previous_gcode_state != self.state.state)  # After server restart
            )
        )
        # For IDLE, only trigger if we just came from RUNNING (explicit abort/cancel)
        if (
            self.state.state == "IDLE"
            and self._previous_gcode_state == "RUNNING"
            and not self._completion_triggered
            and self.on_print_complete
        ):
            should_trigger_completion = True

        if should_trigger_completion:
            if self.state.state == "FINISH":
                status = "completed"
            elif self.state.state == "FAILED":
                status = "failed"
            else:
                status = "aborted"
            logger.info(
                f"[{self.serial_number}] PRINT COMPLETE detected - state: {self.state.state}, "
                f"status: {status}, file: {self._previous_gcode_file or current_file}, "
                f"subtask: {self.state.subtask_name}, was_running: {self._was_running}"
            )
            self._completion_triggered = True
            self._was_running = False
            self.on_print_complete({
                "status": status,
                "filename": self._previous_gcode_file or current_file,
                "subtask_name": self.state.subtask_name,
                "raw_data": data,
            })

        self._previous_gcode_state = self.state.state
        if current_file:
            self._previous_gcode_file = current_file

        if self.on_state_change:
            self.on_state_change(self.state)

    def _request_push_all(self):
        """Request full status update from printer."""
        if self._client:
            message = {"pushing": {"command": "pushall"}}
            self._client.publish(self.topic_publish, json.dumps(message))

    def connect(self):
        """Connect to the printer MQTT broker."""
        self._client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"bambutrack_{self.serial_number}",
            protocol=mqtt.MQTTv311,
        )

        self._client.username_pw_set("bblp", self.access_code)
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message

        # TLS setup - Bambu uses self-signed certs
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        self._client.tls_set_context(ssl_context)

        # Use shorter keepalive (15s) for faster disconnect detection
        # Paho considers connection lost after 1.5x keepalive with no response
        self._client.connect_async(self.ip_address, self.MQTT_PORT, keepalive=15)
        self._client.loop_start()

    def start_print(self, filename: str, plate_id: int = 1):
        """Start a print job on the printer.

        The file should already be uploaded to /cache/ on the printer via FTP.
        """
        if self._client and self.state.connected:
            # Bambu print command format
            # Based on: https://github.com/darkorb/bambu-ftp-and-print
            command = {
                "print": {
                    "sequence_id": 0,
                    "command": "project_file",
                    "param": f"Metadata/plate_{plate_id}.gcode",
                    "subtask_name": filename,
                    "url": f"ftp://{filename}",
                    "timelapse": False,
                    "bed_leveling": True,
                    "flow_cali": True,
                    "vibration_cali": True,
                    "layer_inspect": False,
                    "use_ams": True,
                }
            }
            logger.info(f"[{self.serial_number}] Sending print command: {json.dumps(command)}")
            self._client.publish(self.topic_publish, json.dumps(command))
            return True
        return False

    def stop_print(self) -> bool:
        """Stop the current print job."""
        if self._client and self.state.connected:
            command = {
                "print": {
                    "command": "stop",
                    "sequence_id": "0"
                }
            }
            self._client.publish(self.topic_publish, json.dumps(command))
            logger.info(f"[{self.serial_number}] Sent stop print command")
            return True
        return False

    def disconnect(self):
        """Disconnect from the printer."""
        if self._client:
            self._client.loop_stop()
            self._client.disconnect()
            self._client = None
            self.state.connected = False

    def send_command(self, command: dict):
        """Send a command to the printer."""
        if self._client and self.state.connected:
            # Log outgoing message if logging is enabled
            if self._logging_enabled:
                self._message_log.append(MQTTLogEntry(
                    timestamp=datetime.now().isoformat(),
                    topic=self.topic_publish,
                    direction="out",
                    payload=command,
                ))
            self._client.publish(self.topic_publish, json.dumps(command))

    def enable_logging(self, enabled: bool = True):
        """Enable or disable MQTT message logging."""
        self._logging_enabled = enabled
        # Don't clear logs when stopping - user can manually clear with clear_logs()

    def get_logs(self) -> list[MQTTLogEntry]:
        """Get all logged MQTT messages."""
        return list(self._message_log)

    def clear_logs(self):
        """Clear the message log."""
        self._message_log.clear()

    @property
    def logging_enabled(self) -> bool:
        """Check if logging is enabled."""
        return self._logging_enabled

    def _handle_kprofile_response(self, data: dict):
        """Handle K-profile response from printer."""
        filaments = data.get("filaments", [])
        profiles = []

        # Log first profile to see what fields the printer returns
        if filaments and isinstance(filaments[0], dict):
            logger.debug(f"[{self.serial_number}] Raw K-profile fields: {list(filaments[0].keys())}")
            logger.debug(f"[{self.serial_number}] First K-profile: {filaments[0]}")

        for i, f in enumerate(filaments):
            if isinstance(f, dict):
                try:
                    # cali_idx is the actual slot/calibration index from the printer
                    cali_idx = f.get("cali_idx", i)
                    profiles.append(KProfile(
                        slot_id=cali_idx,
                        extruder_id=int(f.get("extruder_id", 0)),
                        nozzle_id=str(f.get("nozzle_id", "")),
                        nozzle_diameter=str(f.get("nozzle_diameter", "0.4")),
                        filament_id=str(f.get("filament_id", "")),
                        name=str(f.get("name", "")),
                        k_value=str(f.get("k_value", "0.000000")),
                        n_coef=str(f.get("n_coef", "0.000000")),
                        ams_id=int(f.get("ams_id", 0)),
                        tray_id=int(f.get("tray_id", -1)),
                        setting_id=f.get("setting_id"),
                    ))
                except (ValueError, TypeError) as e:
                    logger.warning(f"Failed to parse K-profile: {e}")

        self.state.kprofiles = profiles
        self._kprofile_response_data = profiles

        # Signal that we received the response
        if self._pending_kprofile_response:
            self._pending_kprofile_response.set()

        logger.info(f"[{self.serial_number}] Received {len(profiles)} K-profiles")

    async def get_kprofiles(self, nozzle_diameter: str = "0.4", timeout: float = 5.0) -> list[KProfile]:
        """Request K-profiles from the printer.

        Args:
            nozzle_diameter: Filter by nozzle diameter (e.g., "0.4")
            timeout: Timeout in seconds to wait for response

        Returns:
            List of KProfile objects
        """
        if not self._client or not self.state.connected:
            logger.warning(f"[{self.serial_number}] Cannot get K-profiles: not connected")
            return []

        # Set up response event
        self._sequence_id += 1
        self._pending_kprofile_response = asyncio.Event()
        self._kprofile_response_data = None

        # Send the command
        command = {
            "print": {
                "command": "extrusion_cali_get",
                "filament_id": "",
                "nozzle_diameter": nozzle_diameter,
                "sequence_id": str(self._sequence_id),
            }
        }

        logger.info(f"[{self.serial_number}] Requesting K-profiles for nozzle {nozzle_diameter}")
        self._client.publish(self.topic_publish, json.dumps(command))

        # Wait for response
        try:
            await asyncio.wait_for(self._pending_kprofile_response.wait(), timeout=timeout)
            return self._kprofile_response_data or []
        except asyncio.TimeoutError:
            logger.warning(f"[{self.serial_number}] Timeout waiting for K-profiles response")
            return []
        finally:
            self._pending_kprofile_response = None

    def set_kprofile(
        self,
        filament_id: str,
        name: str,
        k_value: str,
        nozzle_diameter: str = "0.4",
        nozzle_id: str = "HS00-0.4",
        extruder_id: int = 0,
        setting_id: str | None = None,
        slot_id: int = 0,
    ) -> bool:
        """Set/update a K-profile on the printer.

        Args:
            filament_id: Bambu filament identifier
            name: Profile name
            k_value: Pressure advance value (e.g., "0.020000")
            nozzle_diameter: Nozzle diameter (e.g., "0.4")
            nozzle_id: Nozzle identifier (e.g., "HS00-0.4")
            extruder_id: Extruder ID (0 or 1 for dual nozzle)
            setting_id: Existing setting ID for updates, None for new
            slot_id: Calibration index (cali_idx) for the profile

        Returns:
            True if command was sent, False otherwise
        """
        if not self._client or not self.state.connected:
            logger.warning(f"[{self.serial_number}] Cannot set K-profile: not connected")
            return False

        self._sequence_id += 1

        # Build the filament entry - printer uses cali_idx for profile identification
        # For new profiles (slot_id=0), use cali_idx=-1 to tell printer to create new slot
        cali_idx = -1 if slot_id == 0 else slot_id

        # Generate a setting_id for new profiles (required by printer)
        # Format: "PF" + 17 random digits
        import random
        if not setting_id and slot_id == 0:
            setting_id = f"PF{random.randint(10000000000000000, 99999999999999999)}"

        filament_entry = {
            "ams_id": 0,
            "cali_idx": cali_idx,
            "extruder_id": extruder_id,
            "filament_id": filament_id,
            "k_value": k_value,
            "n_coef": "0.000000",
            "name": name,
            "nozzle_diameter": nozzle_diameter,
            "nozzle_id": nozzle_id,
            "setting_id": setting_id,  # Always include setting_id
            "tray_id": -1,
        }

        command = {
            "print": {
                "command": "extrusion_cali_set",
                "filaments": [filament_entry],
                "nozzle_diameter": nozzle_diameter,
                "sequence_id": str(self._sequence_id),
            }
        }

        command_json = json.dumps(command)
        logger.info(f"[{self.serial_number}] Setting K-profile: {name} = {k_value} (cali_idx={cali_idx}, new={slot_id==0})")
        logger.debug(f"[{self.serial_number}] K-profile command: {command_json}")
        self._client.publish(self.topic_publish, command_json)
        return True

    def delete_kprofile(
        self,
        cali_idx: int,
        filament_id: str,
        nozzle_id: str,
        nozzle_diameter: str = "0.4",
        extruder_id: int = 0,
    ) -> bool:
        """Delete a K-profile from the printer.

        Args:
            cali_idx: The calibration index (slot_id) of the profile to delete
            filament_id: Bambu filament identifier
            nozzle_id: Nozzle identifier (e.g., "HH00-0.4")
            nozzle_diameter: Nozzle diameter (e.g., "0.4")
            extruder_id: Extruder ID (0 or 1 for dual nozzle)

        Returns:
            True if command was sent, False otherwise
        """
        if not self._client or not self.state.connected:
            logger.warning(f"[{self.serial_number}] Cannot delete K-profile: not connected")
            return False

        self._sequence_id += 1

        command = {
            "print": {
                "command": "extrusion_cali_del",
                "sequence_id": str(self._sequence_id),
                "extruder_id": extruder_id,
                "nozzle_id": nozzle_id,
                "filament_id": filament_id,
                "cali_idx": cali_idx,
                "nozzle_diameter": nozzle_diameter,
            }
        }

        command_json = json.dumps(command)
        logger.info(f"[{self.serial_number}] Deleting K-profile: cali_idx={cali_idx}, filament={filament_id}")
        logger.debug(f"[{self.serial_number}] K-profile delete command: {command_json}")
        self._client.publish(self.topic_publish, command_json)
        return True

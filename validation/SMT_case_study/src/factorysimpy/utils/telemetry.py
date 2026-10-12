import json
import time
import logging
from typing import Optional, Dict, Any
import pika
import simpy.rt

log = logging.getLogger(__name__)

class RabbitMQSender:
    def __init__(self, host: str = "localhost", queue: str = "telemetry",
                 exchange: str = "manufacturing",
                 max_retries: int = 10, retry_delay: int = 5):
        self.host        = host
        self.queue       = queue
        self.exchange    = exchange
        self.max_retries = max_retries
        self.retry_delay = retry_delay

        self.connection  = None
        self.channel     = None

        self._connect()

    def _connect(self):
        """Connect with retry — waits for RabbitMQ to be ready."""
        for attempt in range(self.max_retries):
            try:
                self.connection = pika.BlockingConnection(
                    pika.ConnectionParameters(
                        host=self.host,
                        credentials=pika.PlainCredentials("guest", "guest"),
                        heartbeat=600,
                        blocked_connection_timeout=300,
                    )
                )
                self.channel = self.connection.channel()

                # Fanout exchange — all consumers get every message
                self.channel.exchange_declare(
                    exchange=self.exchange,
                    exchange_type="fanout",
                    durable=True,
                )
                log.info(f"RabbitMQSender connected to '{self.host}'")
                return

            except Exception as e:
                log.warning(
                    f"RabbitMQ not ready ({attempt+1}/{self.max_retries}): {e}"
                )
                time.sleep(self.retry_delay)

        raise RuntimeError(
            f"Cannot connect to RabbitMQ at '{self.host}' after {self.max_retries} attempts"
        )

    def _ensure_connected(self):
        """Reconnect if the connection dropped between sim runs."""
        try:
            if self.connection and self.connection.is_open:
                return
        except Exception:
            pass
        log.info("RabbitMQ connection lost — reconnecting...")
        self._connect()

    def send(self, message: Dict[str, Any]):
        self._ensure_connected()
        try:
            self.channel.basic_publish(
                exchange=self.exchange,
                routing_key="",           # ignored by fanout
                body=json.dumps(message),
                properties=pika.BasicProperties(delivery_mode=2),
            )
        except Exception as e:
            # Connection may have dropped mid-send — reconnect and retry once
            log.warning(f"Send failed ({e}) — reconnecting and retrying")
            self._connect()
            self.channel.basic_publish(
                exchange=self.exchange,
                routing_key="",
                body=json.dumps(message),
                properties=pika.BasicProperties(delivery_mode=2),
            )

    def close(self):
        try:
            if self.connection and self.connection.is_open:
                self.connection.close()
        except Exception:
            pass



class Telemetry:
    """
    Unified telemetry interface for:
    - Events (instant)
    - Metrics (periodic)
    """

    def __init__(
        self,
        sender: RabbitMQSender,
        simulation: bool = True,
        system_name: str = "FactorySimPy",
        env=None,
    ):
        self.sender = sender
        self.simulation = simulation
        self.system_name = system_name
        self.env = env

    # -----------------------------
    # Internal helpers
    # -----------------------------
    
    
    def _timestamp(self) -> float:
        """
        Always use real wall-clock time for storage
        """
        return time.time()

    def _timing(self, sim_time: Optional[float]) -> Dict[str, Any]:
        timing = {
            "sim_time": sim_time,
            "timestamp": self._timestamp(),
        }
        if isinstance(self.env, simpy.rt.RealtimeEnvironment):
            current_sim_time = self.env.now if sim_time is None else sim_time
            wall_start = time.time() - (time.monotonic() - self.env.real_start)
            timing.update({
                "rt_factor": self.env.factor,
                "scheduled_timestamp": wall_start + (
                    (current_sim_time - self.env.env_start) * self.env.factor
                ),
            })
        return timing

    def _base_payload(self, event_type: str, sim_time: Optional[float]):
        payload = {
            "system": self.system_name,
            "type": event_type,  # "event" or "metric"
        }
        payload.update(self._timing(sim_time))
        return payload

    def _send(self, payload: Dict[str, Any]):
        try:
            self.sender.send(payload)
        except Exception as e:
            # lightweight fallback (no crash)
            print(f"[Telemetry ERROR] {e}")

            #

    # -----------------------------
    # Public API
    # -----------------------------
    def event(
        self,
        event_type: str,
        entity_type: str,
        entity_id: str,
        sim_time: Optional[float] = None,
        attributes: Optional[Dict[str, Any]] = None,
    ): 
        """
        Send an event (instant)
        """
        payload = self._base_payload("event", sim_time)

        # payload.update({
        #     "process": name,
        #     "entity_id": entity_id,
        #     "process_delay": attributes.get("process_delay") if attributes else None, 
        #     "timestamp": self._timestamp()
        
        # })

        payload.update({
            "event_type": event_type,                    # also fix naming
            "entity_type": entity_type,
            "entity_id": entity_id,
            "sim_time": sim_time,
            "timestamp": self._timestamp(),
            "attributes": attributes or {}   # 🔥 ADD THIS
        })

        self._send(payload)

    def metric_old(
        self,
        name: str,
        value: float,
        entity_type: str,
        entity_id: str,
        sim_time: Optional[float] = None,
        attributes: Optional[Dict[str, Any]] = None,
    ):
        """
        Send a metric (periodic)
        """
        payload = self._base_payload("metric", sim_time)

        payload.update({
            "metric": name,
            "value": value,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "attributes": attributes or {},
            
        })



        print(f"[Telemetry] Sending metric: {payload}")
        self._send(payload)


    def publish_kpi(
        self,
        kpi_type: str,
        value: float,
        entity_type: str = "system",
        entity_id: str = "system",
        sim_time: Optional[float] = None,
        attributes: Optional[Dict[str, Any]] = None,
    ):
        """
        KPI is just a specialized metric
        """
        payload = self._base_payload("metric", sim_time)

        payload.update({
            "metric": kpi_type,        # <-- reuse metric field
            "value": float(value),
            "entity_type": entity_type,
            "entity_id": entity_id,
            "sim_time": sim_time,
            "timestamp": self._timestamp(),
            "attributes": attributes or {},
            "is_kpi": True             # optional flag
        })

        self._send(payload)

    def close(self):
        self.sender.close()

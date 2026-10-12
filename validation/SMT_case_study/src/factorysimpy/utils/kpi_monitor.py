from factorysimpy.utils.telemetry import Telemetry
from factorysimpy.utils.telemetry import RabbitMQSender

class KPIMonitor:

    def __init__(self, env, telemetry, sink, machines, sources):
        self.env = env
        self.telemetry = telemetry
        self.sink = sink
        self.machines = machines
        self.sources = sources

    def run(self):
        while True:
            yield self.env.timeout(30)   # every 10 time units

            now = self.env.now

            # -----------------------------
            # SYSTEM KPIs
            # -----------------------------
            total_items = self.sink.stats["num_item_received"]

            if now > 0:
                throughput = total_items / now
                self.telemetry.publish_kpi(
                    "throughput",
                    throughput,
                    entity_type="system",
                    entity_id="system",
                    sim_time=now
                )

            tot_cycletime = self.sink.stats["total_cycle_time"]

            avg_ct = (
                tot_cycletime / total_items
                if total_items > 0 else 0
            )

            self.telemetry.publish_kpi(
                "cycle_time",
                avg_ct,
                entity_type="system",
                entity_id="system",
                sim_time=now
            )

            # -----------------------------
            # SOURCE KPIs
            # -----------------------------
            for src in self.sources:
                generated = src.stats["num_item_generated"]

                self.telemetry.publish_kpi(
                    "items_generated",
                    generated,
                    entity_type="source",
                    entity_id=src.id,
                    sim_time=now
                )

            # -----------------------------
            # MACHINE KPIs
            # -----------------------------
            for machine in self.machines:

                processed = machine.stats["num_item_processed"]

                self.telemetry.publish_kpi(
                    "items_processed",
                    processed,
                    entity_type="machine",
                    entity_id=machine.id,
                    sim_time=now
                )

                # ---- Utilization (basic version) ----
                if now > 0:
                    util = processed / now   # replace later with busy_time
                else:
                    util = 0

                self.telemetry.publish_kpi(
                    "utilization",
                    util,
                    entity_type="machine",
                    entity_id=machine.id,
                    sim_time=now
                )

                # ---- Blocked ratio ----
                blocked = machine.per_thread_total_time_in_blocked_state
                processing = machine.per_thread_total_time_in_processing_state

                total = blocked + processing

                blocked_ratio = (blocked / total) if total > 0 else 0

                self.telemetry.publish_kpi(
                    "blocked_ratio",
                    blocked_ratio,
                    entity_type="machine",
                    entity_id=machine.id,
                    sim_time=now
                )
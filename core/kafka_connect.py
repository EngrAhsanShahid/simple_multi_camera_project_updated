import json
import queue as _queue
from confluent_kafka import Producer


def producer_program(alert_queue, bootstrap_server, topic, stop_event):
    def delivery_report(err, _msg):
        if err is not None:
            print(f"Message delivery failed: {err}")

    config = {"bootstrap.servers": bootstrap_server}
    producer = Producer(config)

    while not stop_event.is_set():
        try:
            message = alert_queue.get(timeout=1)  # unblocks every second to check stop_event
        except _queue.Empty:
            continue
        if message:
            producer.produce(topic, value=json.dumps(message), callback=delivery_report)
            producer.flush()

    print("Kafka producer thread shutting down...")


class KafkaProducer:
    def __init__(self, bootstrap_server, topic):
        self.config = {
            'bootstrap.servers': bootstrap_server
        }
        self.topic = topic
        self.producer = Producer(self.config)

    @staticmethod
    def delivery_report(err, _msg):
        if err is not None:
            print(f"Message delivery failed: {err}")
        else:
            # print(f"Message delivered to {msg.topic()} [{msg.partition()}]")
            pass

    def publish_message(self, messages):
        if messages:
            self.producer.produce_batch(topic=self.topic, messages=messages, callback=self.delivery_report)

    

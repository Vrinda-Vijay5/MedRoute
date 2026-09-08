import time
import random


class HealthcareTrafficGenerator:

    TRAFFIC_TYPES = [
        "ECG",
        "ICU",
        "TELEMEDICINE",
        "EHR",
        "IMAGING",
        "CCTV"
    ]

    def generate(self, count=10):

        traffic = []

        for i in range(count):

            traffic_type = random.choice(
                self.TRAFFIC_TYPES
            )

            packet = {
                "id": i + 1,
                "type": traffic_type,
                "timestamp": time.time()
            }

            traffic.append(packet)

        return traffic


if __name__ == "__main__":

    generator = HealthcareTrafficGenerator()

    traffic = generator.generate(10)

    print("============================")
    print("MedRoute Healthcare Traffic")
    print("Generator Test")
    print("============================")

    for packet in traffic:

        print(
            "Packet ID: {} | Type: {}".format(
                packet["id"],
                packet["type"]
            )
        )

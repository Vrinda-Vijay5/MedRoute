from traffic_generator import HealthcareTrafficGenerator
from traffic_classifier import HealthcareTrafficClassifier


class HealthcarePipeline:

    def __init__(self):

        self.generator = HealthcareTrafficGenerator()
        self.classifier = HealthcareTrafficClassifier()

    def process(self, count=10):

        traffic = self.generator.generate(count)

        results = []

        for packet in traffic:

            classification = self.classifier.classify(
                packet["type"]
            )

            packet.update(classification)

            results.append(packet)

        return results


if __name__ == "__main__":

    pipeline = HealthcarePipeline()

    results = pipeline.process(10)

    print("============================")
    print("MedRoute Healthcare Pipeline")
    print("============================")

    for packet in results:

        print(
            "ID: {} | Type: {} | Class: {} | Criticality: {}".format(
                packet["id"],
                packet["traffic_type"],
                packet["class"],
                packet["criticality"]
            )
        )

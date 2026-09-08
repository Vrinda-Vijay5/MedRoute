class HealthcareTrafficClassifier:

    TRAFFIC_TYPES = {
        "ECG": {
            "class": "EMERGENCY",
            "criticality": 5
        },
        "ICU": {
            "class": "CRITICAL",
            "criticality": 5
        },
        "TELEMEDICINE": {
            "class": "HIGH",
            "criticality": 4
        },
        "EHR": {
            "class": "HIGH",
            "criticality": 4
        },
        "IMAGING": {
            "class": "MEDIUM",
            "criticality": 3
        },
        "CCTV": {
            "class": "LOW",
            "criticality": 1
        }
    }

    def classify(self, traffic_type):

        traffic_type = traffic_type.upper()

        if traffic_type not in self.TRAFFIC_TYPES:
            return {
                "traffic_type": traffic_type,
                "class": "UNKNOWN",
                "criticality": 0
            }

        result = self.TRAFFIC_TYPES[traffic_type]

        return {
            "traffic_type": traffic_type,
            "class": result["class"],
            "criticality": result["criticality"]
        }


if __name__ == "__main__":

    classifier = HealthcareTrafficClassifier()

    traffic_samples = [
        "ECG",
        "ICU",
        "TELEMEDICINE",
        "EHR",
        "IMAGING",
        "CCTV"
    ]

    print("============================")
    print("MedRoute Healthcare Traffic")
    print("Classification Test")
    print("============================")

    for traffic in traffic_samples:

        result = classifier.classify(traffic)

        print(
            "Traffic: {} | Class: {} | Criticality: {}".format(
                result["traffic_type"],
                result["class"],
                result["criticality"]
            )
        )

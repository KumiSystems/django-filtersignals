import filtersignals

signal = filtersignals.FilterSignal()

class TestFilter:
    def __init__(self):
        processed = signal.send(self.__class__, this="great")
        if processed["this"] == "amazing":
            print("Test passed.")

@filtersignals.receiver(signal, priority=1, sender=TestFilter)
def second_receiver(sender, **kwargs):
    return

@filtersignals.receiver(signal, priority=2, sender=TestFilter)
def third_receiver(sender, **kwargs):
    if kwargs["this"] == "better":
        return {"this": "amazing"}
    return {"this": "sucks a lot"}

@filtersignals.receiver(signal, priority=0, sender=TestFilter)
def first_receiver(sender, **kwargs):
    if kwargs["this"] == "great":
        return {"this": "better"}
    return {"this": "sucks"}

TestFilter()
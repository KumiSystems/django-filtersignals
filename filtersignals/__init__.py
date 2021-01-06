from django.dispatch.dispatcher import Signal, NO_RECEIVERS, _make_id, NONE_ID
from django.utils.inspect import func_accepts_kwargs

import weakref

class FilterSignal(Signal):
    def connect(self, receiver, priority, sender=None, weak=True, dispatch_uid=None):
        """
        Connect receiver to sender for signal.

        Arguments:

            receiver
                A function or an instance method which is to receive signals.
                Receivers must be hashable objects.

                If weak is True, then receiver must be weak referenceable.

                Receivers must be able to accept keyword arguments.

                If a receiver is connected with a dispatch_uid argument, it
                will not be added if another receiver was already connected
                with that dispatch_uid.

            priority
                A numerical value indicating the order in which receivers are
                called. The higher the value, the lower the priority.

            sender
                The sender to which the receiver should respond. Must either be
                a Python object, or None to receive events from any sender.

            weak
                Whether to use weak references to the receiver. By default, the
                module will attempt to use weak references to the receiver
                objects. If this parameter is false, then strong references will
                be used.

            dispatch_uid
                An identifier used to uniquely identify a particular instance of
                a receiver. This will usually be a string, though it may be
                anything hashable.
        """
        from django.conf import settings

        # If DEBUG is on, check that we got a good receiver
        if settings.configured and settings.DEBUG:
            assert callable(receiver), "Signal receivers must be callable."

            # Check for **kwargs
            if not func_accepts_kwargs(receiver):
                raise ValueError("Signal receivers must accept keyword arguments (**kwargs).")

        if dispatch_uid:
            lookup_key = (dispatch_uid, _make_id(sender))
        else:
            lookup_key = (_make_id(receiver), _make_id(sender))

        if weak:
            ref = weakref.ref
            receiver_object = receiver
            # Check for bound methods
            if hasattr(receiver, '__self__') and hasattr(receiver, '__func__'):
                ref = weakref.WeakMethod
                receiver_object = receiver.__self__
            receiver = ref(receiver)
            weakref.finalize(receiver_object, self._remove_receiver)

        with self.lock:
            self._clear_dead_receivers()
            if not any(r_key == lookup_key for r_key, _, __ in self.receivers):
                self.receivers.append((lookup_key, receiver, priority))
            self.sender_receivers_cache.clear()

    def disconnect(self, receiver=None, sender=None, dispatch_uid=None):
        """
        Disconnect receiver from sender for signal.

        If weak references are used, disconnect need not be called. The receiver
        will be removed from dispatch automatically.

        Arguments:

            receiver
                The registered receiver to disconnect. May be none if
                dispatch_uid is specified.

            sender
                The registered sender to disconnect

            dispatch_uid
                the unique identifier of the receiver to disconnect
        """
        if dispatch_uid:
            lookup_key = (dispatch_uid, _make_id(sender))
        else:
            lookup_key = (_make_id(receiver), _make_id(sender))

        disconnected = False
        with self.lock:
            self._clear_dead_receivers()
            for index in range(len(self.receivers)):
                (r_key, _, __) = self.receivers[index]
                if r_key == lookup_key:
                    disconnected = True
                    del self.receivers[index]
                    break
            self.sender_receivers_cache.clear()
        return disconnected

    def send(self, sender, **named):
        """
        Send signal from sender to all connected receivers.

        If any receiver raises an error, the error propagates back through send,
        terminating the dispatch loop. So it's possible that all receivers
        won't be called if an error is raised.

        Receivers must return a dict with argument names from **named as keys
        and the new data as values. If a key is not provided, its value is
        unchanged.

        Arguments:

            sender
                The sender of the signal. Either a specific object or None.

            named
                Named arguments which will be passed to receivers.

        Return a dict of values processed by receivers.
        """
        if not self.receivers or self.sender_receivers_cache.get(sender) is NO_RECEIVERS:
            return named

        named["_protected"] = named["_protected"] if "_protected" in named.keys() else []

        responses = named

        receivers = self._live_receivers(sender)
        receivers.sort(key=lambda x:x[1])

        for receiver, _ in receivers:
            response = receiver(signal=self, sender=sender, **responses)
            for key, value in response.items():
                if not key in responses["_protected"]:
                    responses[key] = value
        
        return responses

    def send_robust(self, sender, **named):
        """
        Send signal from sender to all connected receivers catching errors.

        Arguments:

            sender
                The sender of the signal. Can be any Python object (normally one
                registered with a connect if you actually want something to
                occur).

            named
                Named arguments which will be passed to receivers. These
                arguments must be a subset of the argument names defined in
                providing_args.

        Return a list of tuple pairs [(receiver, response), ... ].

        Return a dict of values processed by receivers.

        If any receiver raises an error (specifically any subclass of
        Exception), return it as a (receiver, error) tuple under the 
        "_errors" key.
        """
        if not self.receivers or self.sender_receivers_cache.get(sender) is NO_RECEIVERS:
            return []

        named["_protected"] = named["_protected"] if "_protected" in named.keys() else []

        # Call each receiver with whatever arguments it can accept.
        # Return the updated dict
        responses = named
        responses["_errors"] = []

        receivers = self._live_receivers(sender)
        receivers.sort(key=lambda x:x[1])

        for receiver, _ in receivers:
            try:
                response = receiver(signal=self, sender=sender, **responses)
                for key, value in response.items():
                    if not key in responses["_protected"]:
                        responses[key] = value
            except Exception as err:
                responses["_errors"].append((receiver, err))

        return responses

    def _live_receivers(self, sender):
        """
        Filter sequence of receivers to get resolved, live receivers.

        This checks for weak references and resolves them, then returning only
        live receivers.
        """
        receivers = None
        if self.use_caching and not self._dead_receivers:
            receivers = self.sender_receivers_cache.get(sender)
            # We could end up here with NO_RECEIVERS even if we do check this case in
            # .send() prior to calling _live_receivers() due to concurrent .send() call.
            if receivers is NO_RECEIVERS:
                return []
        if receivers is None:
            with self.lock:
                self._clear_dead_receivers()
                senderkey = _make_id(sender)
                receivers = []
                for (_, r_senderkey), receiver, priority in self.receivers:
                    if r_senderkey == NONE_ID or r_senderkey == senderkey:
                        receivers.append((receiver, priority))
                if self.use_caching:
                    if not receivers:
                        self.sender_receivers_cache[sender] = NO_RECEIVERS
                    else:
                        # Note, we must cache the weakref versions.
                        self.sender_receivers_cache[sender] = receivers
        non_weak_receivers = []
        for receiver, priority in receivers:
            if isinstance(receiver, weakref.ReferenceType):
                # Dereference the weak reference.
                receiver = receiver()
                if receiver is not None:
                    non_weak_receivers.append((receiver, priority))
            else:
                non_weak_receivers.append((receiver, priority))
        return non_weak_receivers

def receiver(signal, priority, **kwargs):
    """
    A decorator for connecting receivers to signals. Used by passing in the
    signal (or list of signals), the requested priority, and keyword 
    arguments to connect:

        @receiver(post_save, 0, sender=MyModel)
        def signal_receiver(sender, **kwargs):
            ...

        @receiver([post_save, post_delete], 0, sender=MyModel)
        def signals_receiver(sender, **kwargs):
            ...
    """
    def _decorator(func):
        if isinstance(signal, (list, tuple)):
            for s in signal:
                s.connect(func, priority **kwargs)
        else:
            signal.connect(func, priority, **kwargs)
        return func
    return _decorator
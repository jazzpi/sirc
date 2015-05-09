# Simply IRC

This is a easy-to-use IRC library mainly focused on the Twitch.TV IRC interface
based on asynchat. To use it, simply create a class and make it inherit from
`IRCConnection` or `TwitchIRCClient`:

```python
class MyTwitchBot(TwitchIRCClient):

    def __init__(self, *args, **kwargs):
        # Call the __init__ method in the superclass to initialize the
        # connection
        super().__init__(*args, **kwargs)
        self.join_channel("#twitchusername")

    def handle_privmsg(self, channel, user, msg):
        # Overwrite this method to handle chat messages
        if msg.lower().startswith("!hello"):
            self.queue_message(channel, "Hello {}! I'm a bot!".format(user))

    def handle_join(self, channel, user):
        # Overwrite this method to handle users joining the chat
        super().handle_join(channel, user)
        self.queue_message(channel, "Welcome {} to the chat :)".format(user))

    def handle_part(self, channel, user):
        super().handle_part(channel, user)
        self.queue_message(channel, "Goobye {} :(".format(user))
```

For more information on the methods, look into the source code and the
docstrings.

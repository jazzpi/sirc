"""Simply IRC.
An easy-to-use IRC library based on asynchat.async_chat.
"""

import asynchat
import asyncore
import logging
import re
import socket
import sys
import threading
import time

import schedule


class InvalidMessageException(Exception):
    """Raised when an IRC message doesn't comply to RFC 1459."""

    def __init__(self, value):
        self.value = value

    def __str__(self):
        return repr(self.value)


def parse_message(message):
    """Parses a message by the definition in RFC 1459, section 2.3.1"""
    prefix = None
    if message[0] == ":":
        m = re.search(" +", message)
        if m is not None:
            prefix = parse_prefix(message[1:m.start()])
            message = message[m.end():]
    command = None
    if message[0:3].isnumeric():
        command = int(message[0:3])
        message = message[3:]
    else:
        m = re.match("([a-zA-Z]+) ", message)
        if m is not None:
            command = m.group(1)
        else:
            raise InvalidMessageException("No <command> in " + message)
        message = message[m.end():]
    params = parse_params(message)
    return (prefix, command, tuple(params))


def parse_prefix(prefix):
    """Parses a prefix by the definition in RFC 1459, section 2.3.1"""
    m = re.match("([a-zA-Z0-9._]+)(!(\w+))?(@([a-zA-Z0-9._]+))?", prefix)
    return (m.group(1), m.group(3), m.group(5))


def parse_params(params):
    if params == " " or params == "":
        return []
    m = re.match(" *:", params)
    if m is not None:
        return [params[m.end():]]
    m = re.search(" *([^ ]+)( *)", params)
    if m is not None:
        return [m.group(1)] + parse_params(params[m.end() - len(m.group(2)):])


class IRCConnection(asynchat.async_chat):
    """A basic IRC connection, mainly just a small extension to
    asynchat.async_chat.
    You shouldn't use this, use a higher abstraction level Client
    instead, or write your own deriving from this.
    """

    def __init__(self, addr, port, nick, user=None, pw=None):
        """Connect to addr:port and initialize the connection.
        To start the main loop, use run()
        """

        self.logger = logging.getLogger(__name__)
        self.logger.debug("__init__")
        self.addr = addr
        self.port = port
        self.nick = nick
        self.pw = pw
        self.sock = socket.socket()
        self.sock.connect((addr, port))

        asynchat.async_chat.__init__(self, sock=self.sock)

        self.set_terminator(b"\r\n")
        self.ibuffer = []

    def collect_incoming_data(self, data):
        """Appends incoming data to buffer."""
        self.ibuffer.append(data)

    def found_terminator(self):
        """Handles incoming messages by decoding and logging, then
        checking if it matches something  in self.message_matches
        """
        msg = ""
        for i in self.ibuffer:
            msg += i.decode()
        self.logger.info("< %s", repr(msg)[1:-1])
        self.ibuffer = []
        self.on_message(parse_message(msg))

    def on_message(self, msg):
        pass

    def write(self, msg):
        """Logs the message, then sends it to the IRC Server."""
        self.logger.info("> %s", repr(msg.decode())[1:-1])
        self.push(msg)

    def login(self):
        """Logs in to the server with the nick, user (opt) and password
        (opt) passed in __init__
        """
        if self.pw is not None:
            self.write(b"PASS " + bytes(self.pw, "utf-8") + b"\r\n")
        self.write(b"NICK " + bytes(self.nick, "utf-8") + b"\r\n")

    def run(self):
        """Logs in and starts the asyncore loop."""
        self.login()
        threading.Thread(target=asyncore.loop).start()


class TwitchIRCClient(IRCConnection):
    """An IRC client for use with the Twitch.TV IRC servers."""

    def __init__(self, *args, **kwargs):
        """Initialize client. Also see IRCConnection.__init__"""
        super().__init__(*args, **kwargs)
        self.channels = dict()
        self.message_queue = []
        self.server_ready = False
        schedule.every(1.5).seconds.do(self.fetch_message)

    def queue_irc_message(self, msg):
        """Queues an IRC message for writing ASAP. Don't use this unless
        you know what you're doing.
        """
        self.message_queue.append(msg)

    def queue_message(self, channel, msg):
        """Queues a chat message `msg` on `channel`."""
        self.message_queue.append(bytes("PRIVMSG {} :{}\r\n".format(channel,
                                        msg), "utf-8"))

    def fetch_message(self):
        """Sends one message from the message queue. Don't call this or
        you might get locked out from the IRC server.
        """
        if not self.server_ready or len(self.message_queue) == 0:
            return
        # self.logger.debug("FETCHED %s", self.message_queue.pop(0))
        self.write(self.message_queue.pop(0))

    def on_message(self, msg):
        """Handles incoming messages by detecting what command was sent
        and then sending the appropriate information to the handlers.
        """
        if isinstance(msg[1], int):
            self.handle_numeric(msg[1], msg[2])
        elif msg[1] == "PRIVMSG":
            self.handle_privmsg(msg[2][0], msg[0][0], msg[2][1])
        elif msg[1] == "PING":
            self.handle_ping(msg[2][0])
        elif msg[1] == "JOIN":
            self.handle_join(msg[2][0], msg[0][0])
        elif msg[1] == "PART":
            self.handle_part(msg[2][0], msg[0][0])
        elif msg[1] == "MODE":
            self.handle_mode(*msg[2])
        elif msg[1] == "NOTICE":
            self.handle_notice(msg[2])
        else:
            self.logger.warn("Server sent an unknown command: %s", msg)

    def handle_numeric(self, number, params):
        """Handles numeric commands."""
        # Ignore some messages:
        if number in (
                1, 2, 3, 4,   # Welcome messages
                375, 372,     # MOTD messages (except for end)
                366):         # END OF NAMES message
            return
        if number == 353:
            # List of names. Sent in non-standard format
            # <nick> = <channel> :<list of nicks>
            # so params[2] is channel name, params[3] is list of nicks
            if self.channels.get(params[2]) is None:
                self.channels[params[2]] = {"users": [], "ops": []}
            # List of nicks is space-separated
            for i in params[3].split(" "):
                self.channels[params[2]]["users"].append(i)
        elif number == 376:
            # End of MOTD command means the server is ready for our
            # messages
            self.server_ready = True
        elif number == 421:
            self.logger.warn(
                "Server responded with 421 (Unknown command): %s", params[1])

    def handle_privmsg(self, channel, user, message):
        """Handles PRIVMSG commands (aka chat messages)."""
        pass

    def handle_notice(self, params):
        """Handles NOTICE commands."""
        if len(params) == 2 and params[0] == "*" and\
                params[1] == "Login unsuccessful":
            self.logger.fatal("Login unsuccessful - Check your nick and pw.")
            self.close()

    def handle_ping(self, address):
        """Handles PING commands by responding with a PONG."""
        self.queue_irc_message(b"PONG :" + bytes(address, "utf-8") + b"\r\n")

    def handle_join(self, channel, user):
        """Handles JOIN commands by adding the user to the user list."""
        if self.channels.get(channel) is None:
            self.logger.warn(
                "Server sent a JOIN for an unknown channel: %s JOIN %s", user,
                channel)
            return
        self.channels[channel]["users"].append(user)

    def handle_part(self, channel, user):
        """Handles PART commands by removing the user from the user
        list.
        """
        if self.channels.get(channel) is None:
            self.logger.warn(
                "Server sent a PART for an unknown channel: %s PART %s", user,
                channel)
            return
        try:
            self.channels[channel]["users"].remove(user)
        # Twitch sometimes messes up...
        except ValueError:
            pass

    def handle_mode(self, channel, mode, user):
        """Handles MODE commands by adding the user to the ops list."""
        if self.channels.get(channel) is None:
            self.logger.warn(
                "Server sent a MODE for an unknown channel: %s MODE %s %s",
                channel, mode, user)
            return
        if mode == "+o":
            self.channels[channel]["ops"].append(user)
        elif mode == "-o":
            try:
                self.channels[channel]["ops"].remove(user)
            # See handle_part
            except ValueError:
                pass
        else:
            self.logger.warn("Server sent an unknown MODE: %s", mode)
            return

    def join_channel(self, channel):
        """Joins a channel."""
        self.queue_irc_message(b"JOIN " + bytes(channel, "utf-8") + b"\r\n")
        self.channels[channel] = {"users": [], "ops": []}

    def schedule_loop(self):
        """Loop for the schedule module. Shouldn't really be called."""
        while True:
            try:
                schedule.run_pending()
            except ConnectionResetError:
                self.logger.critical("Connection reset by peer. Exiting.")
                sys.exit(104)
            time.sleep(0.5)

    def run(self):
        """Logs in and starts the asyncore and schedule loop."""
        super().run()
        threading.Thread(target=self.schedule_loop).start()

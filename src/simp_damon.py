###############################################################################
# SIMP (Simple Instant Messaging Protocol) Daemon Implementation
#
# This implementation provides a UDP-based chat system with:
# - Daemon-to-daemon communication for chat messages
# - Client-to-daemon communication for local control
# - Reliable message delivery using Stop-and-Wait protocol
# - Chat session management and error handling
###############################################################################

import socket # For network communication
import threading  # to run multiple tasks simultaneously
import json     #to handle Json data (used for communication with the client)
import struct   #for packing and unpacking binary data (headers
import time     #For timeout handling and timestamps
from enum import Enum   #For defining message states
import logging  #For system logging and debugging


#Enum for tracking the state of sent messages
class MessageState(Enum):
    WAITING = 1     #Message sent, waiting for ACK
    ACKNOWLEDGED = 2   # Message succesfully ACK by receiver
    FAILED = 3      #Message failed after max retries

class SIMPDaemon:

    """
    Main daemon class implementing the SIMP protocol.
    Handles both daemon-to-daemon and client-to-daemon communication.
    """

    def __init__(self, host):
        self.host = host
        self.daemon_port = 7777 # Port for daemon-to-daemon communication
        self.client_port = 7778 # Port for client-to-deamon communication
        self.timeout = 5.0      #Socket timeout in seconds
        self.max_retries = 3    #Maximum message retransmission attempts

        #Initialize UDP sockets
        self.daemon_sock = self.setup_socket(self.daemon_port)  #For daemon communication
        self.client_sock = self.setup_socket(self.client_port)  #For client documentation

        #chat sesstion state
        self.current_chat = None    #stores details of the current chat connection
        self.sequence = 0  # Sequence number for Stop-and-Wait protocol
        self.username = None    #Username of the local client
        self.client_addr = None #Address of the local client (IP and port)
        self.pending_request = None #Holds an incoming chat request (before acceptance)

        # Message tracking and cleanup
        self.message_states = {}    #Tracks state of sent messages
        self.cleanup_threshold = 300#Message state cleanup threshold (5 minutes)

        #Setup logging
        logging.basicConfig(level=logging.INFO)

        self.logger = logging.getLogger('SIMPDaemon')

    def advance_sequence(self):
        """ Advancesthe sequence number for Stop -and -Wait protocol (alternates between 0 and 1)"""
        self.sequence = (self.sequence + 1) % 2

    def accept_chat(self):
        """
        Handles chat request acceptance by:
        1. Sending SYN+ACK to requesting daemon
        2. Establishing chat session
        3. Notifying local client
        """
        if not self.pending_request:
            return

        try:
            # Send  SYN+ACK
            header = self.create_header(0x01, 0x06)  # 0x06 = SYN|ACK
            self.send_with_retry(header, self.pending_request['addr'])

            self.current_chat = {
                'addr': self.pending_request['addr'],
                'username': self.pending_request['username'],
                'state': 'established'
            }

            self.send_to_client({
                'type': 'chat_started',
                'with': self.pending_request['username']
            })

            self.pending_request = None

        except Exception as e:
            self.logger.error(f'Error accepting chat: {e}')
            self.notify_client_of_error('Failed to accept chat')

    def check_timeouts(self):
        current_time = time.time()
        for msg_id, state in list(self.message_states.items()):
            if state['state'] == MessageState.WAITING:
                if current_time - state['timestamp'] > self.timeout:
                    if state['retries'] < self.max_retries:
                        self.logger.info(f'Retransmitting message {msg_id}')
                        self.retransmit_message(msg_id)
                    else:
                        self.logger.error(f"Message {msg_id} failed after max retries")
                        state['state'] = MessageState.FAILED
                        self.handle_transmission_failure(msg_id)
    def cleanup(self):
        if self.daemon_sock:
            self.daemon_sock.close()
        if self.client_sock:
            self.client_sock.close()

    def decline_chat(self):
        if not self.pending_request:
            return

        try:
            #send FIN
            self.send_fin(self.pending_request['addr'])
            self.pending_request = None

        except Exception as e:
            self.logger.error(f'Error declining chat: {e}')
            self.notify_client_of_error('Failed to decline chat')

    def end_chat(self):
        if not self.current_chat:
            return

        try:
            # send FIN on remote-Daemon
            self.send_fin(self.current_chat['addr'])

            #inform local client
            self.send_to_client({
                'type': 'chat_ended',
            })

            self.current_chat = None

        except Exception as e:
            self.logger.error(f'Error end chat: {e}')

    def get_message_id(self, sequence, addr):
        return f'{sequence}_{addr[0]}_{addr[1]}'

    def handle_daemon_messages(self):
            while True:
                data, addr = self.daemon_sock.recvfrom(1024) #receive up to 1024 bytes
                header = self.parse_header(data)    #extract the header and payload

                if header['type'] == 0x01: #control datagram
                    self.handle_control_message(header, addr)
                elif header['type'] == 0x02: #chat datagram
                    self.handle_chat_message(header, addr)

    def handle_control_message(self, header, addr):
        if not header:
            return

        try:
            if header['operation'] == 0x02:  # SYN
                if self.current_chat is None:
                    self.pending_request = {
                        'addr': addr,
                        'username': header['user']
                    }
                    self.notify_client_of_request()
                else:
                    self.send_error(addr, "User busy")
                    self.send_fin(addr)

            elif header['operation'] == 0x04:  # ACK
                if self.current_chat and addr == self.current_chat['addr']:
                    msg_id = self.get_message_id(header['sequence'], addr)
                    if msg_id in self.message_states:
                        self.message_states[msg_id]['state'] = MessageState.ACKNOWLEDGED
                    self.advance_sequence()

        except Exception as e:
            self.logger.error(f"Control message handling error: {e}")

    def handle_transmission_failure(self, msg_id):
        if self.current_chat:
            self.end_chat()
            self.notify_client_of_error("Connection lost")

    def handle_chat_message(self, header, addr):
        if self.current_chat and addr == self.current_chat['addr']:
            # Forward message to the local client
            message = {
                'type': 'chat_message',
                'from': self.current_chat['username'],  # Sender's username
                'content': header['payload'].decode().strip('\x00')  # Decode and clean payload
            }
            self.send_to_client(message)  # Send the message to the client

            # Send ACK back to the sender
            self.send_ack(addr)

    def handle_client_messages(self):
        while True:
            data, addr = self.client_sock.recvfrom(1024)
            message = json.loads(data.decode())  # decode Json data

            if message['type'] == 'connect':  # client connecting to the daemon
                self.username = message['username']  # store the username
                self.client_addr = addr  # store the client's address
                self.send_to_client({'status': 'ok'})  # Acknowledge connection

            elif message['type'] == 'start_chat':  # client initiates a chat
                self.initiate_chat(message['target_ip'])  # need to be discussed

            elif message['type'] == 'chat_response':  # Response to a chat request
                if message['accept']:  # if accepted
                    self.accept_chat()
                else:  # if declined
                    self.decline_chat()

            elif message['type'] == 'chat_message':  # Message to send
                self.send_chat_message(message['content'])

            elif message['type'] == 'end_chat':  # client ends the chat
                self.end_chat()

    def handle_syn_ack(self, header, addr):
        if self.current_chat and addr == self.current_chat['addr']:
            #send ACK back
            ack_header = self.create_header(0x01, 0x04)
            self.send_with_retry(ack_header, addr)

            self.current_chat['state'] = 'established'
            self.current_chat['username'] = header['user']

            self.send_to_client({
                'type': 'chat_started',
                'with': header['user']
            })

    def initiate_chat(self, target_ip):
        try:
            if self.current_chat:
                self.notify_client_of_error('Already in a chat')
                return

            target_addr = (target_ip, self.daemon_port)

            msg_id = self.send_with_retry(self.create_header(0x01, 0x02), target_addr)

            self.current_chat = {
                'addr': target_addr,
                'username': None, #set by handshake
                'state': 'pending'
            }

        except Exception as e:
            self.logger.error(f'Error initiating chat: {e}')
            self.notify_client_of_error('Failed to initiate chat')
            self.current_chat = None

    def send_chat_message(self, content):
        if not self.current_chat:
            self.notify_client_of_error("Connection lost- not in a chat")
            return

        try:
            header = self.create_header(0x02, 0x01, content.encode())
            msg_id = self.send_with_retry(header, self.current_chat['addr'])

            # wait for ACK
            self.logger.info(f"Send chat message,waiting for ACK (msg_id: {msg_id})")

        except Exception as e:
            self.logger.error(f'Error sending chat message: {e}')
            self.notify_client_of_error('Failed to send chat message')

    def send_fin(self, addr):
        try:
            header = self.create_header(0x01, 0x08)
            self.daemon_sock.sendto(header, addr)

        except Exception as e:
            self.logger.error(f'Error sending FIN: {e}')

    def send_with_retry(self, data, addr, msg_id=None):
        if msg_id is None:
            msg_id = f"{time.time()}_{addr}"

        self.message_states[msg_id] = {  # Corrected indentation
            'data': data,
            'addr': addr,
            'timestamp': time.time(),
            'retries': 0,
            'state': MessageState.WAITING
        }

        try:
            self.daemon_sock.sendto(data, addr)
        except socket.error as e:
            self.logger.error(f'Send error: {e}')
            return False

        return msg_id

    def setup_socket(self, port):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind((self.host, port))
        sock.settimeout(self.timeout)
        return sock

    def start(self):
            try:
                daemon_thread = threading.Thread(target=self.handle_daemon_messages)
                client_thread = threading.Thread(target=self.handle_client_messages)

                daemon_thread.daemon = True
                client_thread.daemon = True

                daemon_thread.start()
                client_thread.start()

                while True:
                    time.sleep(1)
                    self.check_timeouts()

            except Exception as e:
                self.logger.error(f"Critical error: {e}")
                self.cleanup()

    def retransmit_message(self, msg_id):
        state = self.message_states[msg_id]
        state['retries'] += 1
        state['timestamp'] = time.time()

        try:
            self.daemon_sock.sendto(state['data'], state['addr'])
        except socket.error as e:
            self.logger.error(f'Retransmit error: {e}')

    def create_header(self, type: int, operation: int, payload: bytes=b'') -> bytes:
        if not isinstance(payload, bytes):
            payload = b'' if payload is None else str(payload).encode()
        try:
            return struct.pack('!BBB32sI',
                type, #Message type (control/chat)
                    operation,  #Operation code (SYN, ACK)
                    self.sequence,  #sequence number (stop-and-Wait)
                    self.username.encode().ljust(32),   #Username, padded to 32 bytes
                    len(payload)    #length of the payload
                               ) + payload  #append the payload
        except struct.error as e:
            self.logger.error(f'Header creation error: {e}')
            return None


    def parse_header(self, data):
        try:
            header_size = struct.calcsize('!BBB32sI')   #calcualte header size
            if len(data) < header_size:
                raise ValueError("Incomplete header")

            header = struct.unpack('!BBB32sI', data[:header_size]) #unpack header fields
            payload = data[header_size:header_size + header [4]]

            if len(payload) != header[4]:
                raise ValueError("Incomplete payload")

            return {
                'type': header[0],
                'operation': header[1],
                'sequence': header[2],
                'user': header[3].decode(),
                'payload_length': header[4],
                'payload': data[header_size:]   #Extract the payload
            }
        except (struct.error, ValueError) as e:
            self.logger.error(f'Header parse error: {e}')
            return None

    def send_syn(self, addr):
        header = self.create_header(0x01, 0x02)
        self.daemon_sock.sendto(header, addr)

    def send_ack(self, addr):
        header = self.create_header(0x01, 0x04)
        self.daemon_sock.sendto(header, addr)

    def send_error(self, addr, message):
        header = self.create_header(0x01, 0x01, message.encode())
        self.daemon_sock.sendto(header, addr)

    def send_to_client(self, message):
        if self.client_addr: # ensure the client address is known
            self.client_sock.sendto(json.dumps(message), self.client_addr)

    def notify_client_of_request(self):
        if self.client_addr: #ensure the client is connected
            message = {
                'type': 'chat_request',
                'from': self.pending_request['username'], # Requesters username
                'ip': self.pending_request['addr'][0]   # Requester's IP
            }
            self.send_to_client(message)

    def notify_client_of_error(self, error_message):
        if self.client_addr:
            message = {
                'type': 'error',
                'message': error_message
            }
            try:
                self.send_to_client(message)
            except Exception as e:
                self.logger.error(f'Error notifying client: {e}')
# Main script to start the daemon
if __name__ == "__main__":
    import sys
    if len(sys.argv) != 2:  # Ensure correct usage
        print("Usage: python simp_damon.py <host_ip>")
        sys.exit(1)

    daemon = SIMPDaemon(sys.argv[1])    #Create the damon with the given host IP
    daemon.start()  #start the daemon



import socket
import json
import struct
import time
import threading
import sys

class SIMPClient:
 

    def __init__(self, ip):
        self.ip = ip
        self.port = 7778  
        self.username = None
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(5.0) 
        self.running = False

 

    def start(self):
     
        try: 
            self.username = input("Enter your username: ")
            if not self.connect_to_daemon():
                return
            self.running = True
            threading.Thread(target=self.handle_message, daemon=True).start()

            while self.running:
                command = input("Enter command (new, wait, quit): ").strip().lower()
                if command == "new":
                    target_ip = input("Enter the IP address of the user to chat with: ")
                    self.start_chat(target_ip)
                elif command == "wait":
                    print("Waiting for chat requests...") 
                    try:
                        while self.running:
                            self.handle_message
                    except KeyboardInterrupt:
                        print("Stopped waiting for chat requests.")                
                elif command == "quit":
                    self.quit()
                else:
                    print("Unknown command. Please enter 'new', 'wait', or 'quit'.")
        except Exception as e:
            print(f"An unexpected error occurred: {e}")
            self.quit()
    def connect_to_daemon(self):
        message = {
            'type': 'connect',
            'username': self.username
        }
        try:
            self.sock.sendto(json.dumps(message).encode(), (self.ip, self.port))
            response, _ = self.sock.recvfrom(1024)
            response = json.loads(response.decode())
            if response.get('status') == 'ok':
                print("Connected to the daemon.")
                self.running = True
                return True
            else:
                print("Failed to connect to the daemon.")
                self.running = False
                return False
        except Exception as e:
            print(f"Error connecting to the daemon: {e}")

    def start_chat(self, target_ip):
        message = {
            'type': 'start_chat',
            'target_ip': target_ip
        }
        try:
            self.sock.sendto(json.dumps(message).encode(), (self.ip, self.port))
        except Exception as e:
            print(f"Error starting chat: {e}")
    def handle_message(self):

        while self.running:
            try:
                message, _ = self.sock.recvfrom(1024)
                message = json.loads(message.decode())
                if message['type']  == 'chat_request':
                    self.handle_chat_request(message)
                elif message['type'] == 'chat_message':
                    print(f"{message['from']}: {message['content']}")
                elif message['type'] == 'error':
                    print(f"Error: {message['message']}")
                elif message['type'] == 'chat_started':
                    print(f"Chat started with {message['with']}.")
                elif message['type'] == 'chat_ended':
                    print("Chat has ended.")
            except Exception as e:
                print(f"Error receiving message: {e}")

    
    def send_to_daemon(self,message):
        try: 
            self.sock.sendto(json.dumps(message).encode(), (self.ip, self.port))
        except Exception as e:
            print(f"Error sending message to daemon: {e}")
    def handle_chat_request(self, message):
        
        try:
            print(f"Chat request from {message['from']} (IP: {message['ip']}).")
            response = input("Accept? (y/n): ").lower()

            if response == 'y':
                print(f"Accepted chat request")
                self.send_to_daemon({'type': 'chat_response','accept': True})
            else:
                print(f"Declined chat request ")
                self.send_to_daemon({'type': 'chat_response','accept': False})
        except Exception as e:
            print(f"Error handling chat request: {e}")
    
    

    def quit(self):
        """ Disconnect from the daemon and exit the client. """
        self.running = False
        try:
            self.sock.close()
            print("Disconnected from the daemon. Exiting client.")
        except Exception as e:
            print(f"Error during shutdown: {e}")
if __name__ == "__main__":
    if len(sys.argv) != 2:  
        print("Usage: python simp_client.py <daemon_ip>")
        sys.exit(1)

    client = SIMPClient(sys.argv[1])  
    client.start() 

#!/usr/local/bin/python3
#
# Emulates a Global Cache iTach IP2SL (IP to Serial) to provide bidirectional
# TCP-to-serial connections to physical serial ports connected to the host running
# this microservice. Up to eight physical RS232/RS485 serial ports can be exposed
# through the TCP API by a Virtual IP2SL instance.

import logging
from flask import Flask

import re

import atexit
import threading
import socket
import socketserver

import beacon
import util
from listener import start_serial_listeners, get_serial_listeners

log = logging.getLogger(__name__)

FLEX_TCP_API_VERSION = '1.6'
FLEX_TCP_COMMAND_PORT = 4998

# general errors
ERR_INVALID_REQUEST      ='ERR 001'   # Invalid request. Command not found.
ERR_INVALID_SYNTAX       ='ERR 002'   # Bad request syntax used with a known command
ERR_INVALID_MODULE       ='ERR 003'   # Invalid or missing module and/or connector address
ERR_NO_CR                ='ERR 004'   # No carriage return found
ERR_NOT_SUPPORTED        ='ERR 005'   # Command not supported by current Flex Link Port setting
ERR_SETTINGS_LOCKED      ='ERR_006'   # Settings are locked

# serial errors
ERR_INVALID_BAUD_RATE    ='ERR SL001' # Invalid baud rate
ERR_INVALID_FLOW_SETTING ='ERR SL002' # Invalid flow control or duplex setting
ERR_INVALID_PARITY       ='ERR SL003' # Invalid parity setting
ERR_INVALID_STOP_BITS    ='ERR SL004' # Invalid stop bits setting

app = Flask(__name__)

threads = []

util.setup_logging()
log = logging.getLogger(__name__)

config = util.load_config()

class FlexCommandTCPHandler(socketserver.BaseRequestHandler):
    def handle(self):
        self._data = self.request.recv(1024).strip().decode()
        print(f"{self.client_address[0]} sent command: {self._data}")
        log.debug(f"{self.client_address[0]} sent command: %s", self._data)

        print(f"Data {self._data}")
        if self._data.startswith('getdevices'):
            self.handle_getdevices()
        elif self._data.startswith('getversion'):
            self.handle_getversion()
        elif self._data.startswith('get_NET'):
            self.handle_get_NET()
        elif self._data.startswith('get_SERIAL'):
            self.handle_get_SERIAL()
        elif self._data.startswith('set_SERIAL'):
            self.handle_set_SERIAL()
        else:
            self._return_error(ERR_INVALID_REQUEST, "Unknown request: {self._data}")

    def _return_error(self, error_code, message):
        log.error(f"Error reply '{error_code}': {message}")
        self.send_response(error_code)

    def send_response(self, response):
        log.info(f"Sending response: {response}")
        print(f"Sending response: {response}")
        self.request.sendall(response.encode())

    def handle_getdevices(self):
        entries = [ "device,0,0 ETHERNET" ]

        # included all serial ports exposed by configuration
        for port in config['serial'].keys():
            entries.append(f"device,1,{port} SERIAL")
 
        entries.append("endlistdevices")
        response = f"\n".join(entries)
        self.send_response(response)

    def handle_getversion(self):
        self.send_response("710-3000-18") # firmware version part number

    def handle_get_NET(self):
        # the Flex host network module address is always hardcoded 0:1
        #   NET,0:1,<configlock>,<ipsetting>,<ipaddress>,<subnet>,<gateway>
#        if !self._data.startsWith("get_NET,0:1"):
#            throw error
#        host = util.get_host(self._config)
#        gateway = '127.0.0.1'
#        netmask = '255.255.255.0'
#        self.send_response(f"NET,0:1,LOCKED,STATIC,{host},{netmask},{gateway}")
        self._return_error(ERR_NOT_SUPPORTED, "Network lookup not currently supported")

    # response: SERIAL,1:1,<baudrate>,<flowcontrol/duplex>,<parity>,<stopbits>
    def _SERIAL_response(self, port):
        # FIXME: actually get this form the serial object (since it could have changed)
        cfg = config['serial'][port]
        if cfg:
            return f"SERIAL,1:{port},{cfg['baud']},{cfg['flow']},{cfg['parity']},{cfg['stop_bits']}"
        return None # FIXME
   
    def handle_get_SERIAL(self):
        m = re.search("get_SERIAL,1:(?P<port>.+)", self._data)
        if m:
            port = int(m.group('port'))
            if port in config['serial']:
                return self.send_response( _SERIAL_response(port) )

        self._return_error(ERR_INVALID_MODULE, f"Invalid module or port specified for: {self._data}")

    # FIXME: should we persist setting serial this across restarts?
    def handle_set_SERIAL(self):
        # set_SERIAL,<module>:<port>,<baudrate>,<flowcontrol/duplex>,<parity>,[stopbits]
        # set_SERIAL,1:1,115200,FLOW_NONE,PARITY_NO

        # FIXME: handle optional stop bits

        m = re.search("set_SERIAL,1:(?P<port>.+),(?P<baud>.+),(?P<flow>.+),(?P<parity>.+),(?P<stop_bits>.+?)",
                      self._data)
        if m:
            port = int(m.group('port'))
            cfg = config['serial']
            if cfg:
                # update the existing configuration in memory (NOTE: possibly threading issue)
                cfg['baud']      = int(m.group('baud'))
                cfg['flow']      = m.group('flow')
                cfg['parity']    = int(m.group('parity'))

                if m.group('stop_bits'): # handle optional stop bits
                    cfg['stop_bits'] = int(m.group('stop_bits'))

                # update the serial connection with the new configuration
                listeners = get_serial_listeners()
                listeners[port].reset_serial_parameters(cfg)

                return self.send_response( _SERIAL_response(port) )

            else:
                self._return_error(ERR_INVALID_SYNTAX, f"set_SERIAL error! Could not parse: {self._data}")
 
        # always reply with the current configuration
        response = self._prepare_SERIAL_response()
        self.send_response(response)

        self._return_error(ERR_INVALID_MODULE, f"Invalid module or port specified for: {self._data}")


class ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    pass

@app.route('/')
def web_console():
    return '<h1>Virtual iTach Flex Serial Console</h1>'

@atexit.register
def shutdown_listeners():
    log.info("Atexit handler shutdown_listeners()")
    #stop_all_listeners()
    #command_server.shutdown()
    #command_server.server_close()

def start_command_listener():
    host = util.get_host(config)

    log.info(f"Starting Flex TCP command listener at {host}:{FLEX_TCP_COMMAND_PORT}")
    print(f"Starting Flex TCP command listener at {host}:{FLEX_TCP_COMMAND_PORT}")
    server = ThreadedTCPServer((host, FLEX_TCP_COMMAND_PORT), FlexCommandTCPHandler)

    # the command listener is in its own thread which then creates a new thread for each TCP request
    server_thread = threading.Thread(target=server.serve_forever)
    server_thread.daemon = True # exit the server thread when the main thread terminates
    server_thread.start()

    threads.append(server_thread)
    return server

def main():
    ip2sl_beacon = beacon.AMXDiscoveryBeacon(config)
    port_listeners = start_serial_listeners(config)
    command_listener = start_command_listener()

    # FIXME: until Flask http bind issue is resolved, just wait for all threads to complete before exiting
    for a_thread in threads:
        a_thread.join()
    exit

    # run the http console server in the main thread
    host = util.get_host(config)
    console_port = int(os.getenv('IP2SL_CONSOLE_PORT', 4444))

    log.info(f"Starting UI console at http://{host}:{console_port}")
    print(f"Starting UI console at http://{host}:{console_port}")
    app.run(debug=True, host=host, port=console_port) # FIXME: allow env override, but default to 80!

if __name__ == '__main__':
  main()

# Support for Xantech's Multi-Zone matrix audio RS232 protocol.
#
# NOTE: The Monoprice multi-zone amps share a variation of the same protocol as Xantech, and are likely
# licensed (or acquired) from Xantech since they no longer produce multi-zone amplifiers/controllers.
# Monoprice versions has a "channel" concept, into true matrix switching, with selection for each zone
# of either LINE or BUS. Also Monoprice supports chaining together up to 2 additional amps (as slaves),
# with the the NodeJS 6zhmaut API writing "?10\r" if single zone, followed by "?20\r" (if 2), and then
# "?30\r" (if 3) upon startup of the server. 
#       
# "?10\r" = inquiry command structure for all zones of Main Unit 1
# "?20\r" = inquiry command structure for all zones of Main Unit 2
# "?30\r" = inquiry command structure for all zones of Main Unit 3
#
# See: https://downloads.monoprice.com/files/manuals/31028_Manual_180731.pdf
#  
#
# FIXME:
# - Determining what features are supported by doing a zone status query for zone 1,
#   depending on what it returns indicates the feature set
#      - source select (matrix) vs channel select (simple switching)
#      - number of zones
#      - number of sources
#
# Xantech firmware release notes make some mention of RS232 feature changes:
#   https://www.xantech.com/firmware-updates

import re
import logging

from bridge import SerialBridge

log = logging.getLogger(__name__)

class XantechSerialProtocol:
    def __init__(self, serial):
        self._name = 'Xantech'
        self._serial = serial

        self._max_zones = 8
        self._zone_map = {}

        self._max_sources = 8
        self._source_map = {}

        try:
            # According to Monoprice RS232 Control Codes manual, it appears that the serial
            # baud rate defaults to 9600, but can be reconfigured (until next time power is
            # lost to the amplifier) up to 230400 baud. E.g.:
            #  <9600\r
            #  <115200\r
            #
            # Dynamically try baud rates (looking for response) and then increase the baud
            # rate automatically to 115200 (or higher). Note the configured baud rate
            # resets to 9600 after AC power is lost.
            # FIXME
            # self._serial.write_command("<115200")
            # self._serial.raw_serial.setBaudrate(115200)

            serial.write_command("!ZA0+") # disable activity based status updates (0 = true)
            serial.write_command("!ZP0+") # disable periodic status updates (seconds = 0)

            # FIXME: interrogate the Xantech serial device for defaults
            self._discoverDefaultDeviceConfiguration()

        except:
            log.error("Unexpected error: %s", sys.exc_info()[0])
            raise RuntimeError("Failed to initialize Xantech interface")

    def _init_name_mapping(count, name_prefix):
        map = {}
        for i in range(count):
            map[i] = name_prefix + ' ' + i
        return map

    def _deserialize_zone_state_into_map(serialized_state):
        state = {}

        # example Xantech status response:
        #   #{z#}ZS PR{0/1} SS{s#} VO{v#} MU{0/1} TR{bt#} BS{bt#} BA{b#} LS{0/1} PS{0/1}+
        #
        # example Monoprice MPR-SG6Z status response from RS232 docs:
        #   #{z#}ZS VO{v#} PO{0/1} MU{0/1} IS{0/1}+
        #
        # example Monoprice MPR-SG6Z status response in practice:
        #   #1ZS PA0 PR1 MU0 DT0 VO15 TR10 BS12 BL10 CH01 LS0

        for data in serialized_state.upper().split():

            # zone identifier is a special case as it begins with a # instead of the type info
            match = re.search('#(.+?)ZS', data)
            if match:
                state['zone'] = int(match.group(1))

            # for each type of data in the response, map into the state structure
            # (not all may be returned, depending on what features the amplifier supports)
            data_type = data[0:1]
            if data_type in ['PO', 'PR']: # Xantech == PO / Monoprice == PR
                state['power'] = (data[2] == '1') # bool
                
            elif 'SS' == data_type: # Xantech (source select)
                state['source'] = int(data[2:])

            elif 'CH' == data_type: # Monoprice/Daytona Audio (source channel)
                state['source'] = int(data[2:])

            elif 'VO' == data_type:
                # map the 38 physical attenuation levels into 0-100%
                attenuation_level = int(data[2:])
                state['volume'] = round((100 * attenuation_level) / 38)

            elif 'MU' == data_type:
                state['mute'] = (int(data[2:]) == 1) # bool (supports xx1 and xx02)

            elif 'TR' == data_type:
                state['treble'] = int(data[2:])

            elif 'BS' == data_type:
                state['bass'] = int(data[2:])

            elif 'BA' == data_type:
                state['balance'] = int(data[2:])

            elif 'LS' == data_type:
                state['keypad-linked'] = (int(data[2:]) == 1) # bool (supports xx1 and xx02)

            elif 'PS' == data_type: # Xantech
                state['paged'] = (int(data[2:]) == 1) # bool (supports xx1 and xx02)

            elif 'DT' == data_type: # Monoprice/Daytona Audio
                state['do-not-disturb'] = (int(data[2:]) == 1) # bool (supports xx1 and xx02)

            elif 'PA' == data_type: # Monoprice/Daytona Audio
                # note, this setting forces zone 1 to all outputs!!!
                state['public-address-12v-control'] = (int(data[2:]) == 1) # bool (supports xx1 and xx02)

            elif 'IS' == data_type: # Monoprice/Daytona Audio (input select), seen in docs
                if data[2] == '1':  # 0 = BUS, 1 = LINE.
                    state['input'] = 'line'
                else:
                    state['input'] = 'bus'

            else:
                log.warning("Ignoring unknown zone %d state attribute '%s' found in: %s",
                            state['zone'], data_type, serialized_state)

        return state

    def get_zone_state(zone_id):
        if not self.is_valid_zone(zone_id):
            return None

        self._serial.write_command("?" + zone_id + "ZD+")
        state = _deserialize_zone_state_into_map(response)
        
        if state['zone'] != zone_id:
            log.error("Requested state for zone %d, received: %s", zone_id, state)
            return None

        # FIXME: inject friendly name into the state
        state['name'] = 'Unknown'

        return state

    def _discover_default_device_configuration():
        # FIXME: issue commands to identify device

        # determine the number of zones (inefficient, but works)...in future perhaps
        # check 9 and decide tree strategy for narrowing in on the limited set of 
        # possible choices: 4, 6, 8, 16
        for zone_id in range(16):
            if _getZoneSate(zone_id) != None:
                self._max_zones = zone_id
        self._zone_map = _init_name_mapping(self._max_zones, "Zone")

        # NOTE: Monoprice only supports two inputs (bus/line), not source mapping!

        self._source_map = _init_name_mapping(self._max_zones, "Source")

        return

    # the input string should have {} as the substitution token for the zone_id
    def write_to_all_zones(string):
        for zone_id in range(8):
            self._serial.write_command(string.format(zone_id))


    # FIXME: one difference between Xantech and Monoprice is that the
    # prefix for change commands in Xantech is "!" and Monoprice it is ">"

    def is_valid_zone(zone_id):
        if zone_id <= 0:
            return False
        elif zone_id > self._max_zones:
            # FIXME: determine if valid based on configuration for amplifier (model, number of slaves, etc)
            return False
        return True

    def is_valid_source(source_id):
        if source_id <= 0:
            return False
        elif source_id > self._source_zones:
            # FIXME: determine if valid based on configuration for amplifier
            return False
        return True
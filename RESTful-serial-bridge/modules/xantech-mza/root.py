import os   
import json
import logging

from flask import request
from flask_restplus import Resource
from bridge.api.restplus import api

log = logging.getLogger(__name__)


#Flask-RESTPlus provides a way to use almost the same pattern as Flask’s blueprint.
ns = api.namespace('xantech', description='Main audio controller operations')

# example: /api/xantech/power/on
@ns.route('/power/on')
class XantechPowerOn(Resource):
    def get(self):
        raSerial.writeToAllZones("!{}PR1+")
        return {}

@ns.route('/power/off')
class XantechPowerOff(Resource):
    def get(self):
        # Xantech provides a special "All Zones Off" command instead of:
        #   raSerial.writeToAllZones("!{}PR0+")
        raSerial.writeCommand("!AO+")
        return {}

@ns.route('/mute/on')
class XantechPowerOn(Resource):
    def get(self):
        raSerial.writeToAllZones("!{}MU1+")
        return {}

@ns.route('/mute/off')
class XantechPowerOff(Resource):
    def get(self):
        raSerial.writeToAllZones("!{}MU0+")
        return {}

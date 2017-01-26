# coding=utf-8
import logging
import smbus
import time
from .base_sensor import AbstractSensor

logger = logging.getLogger("mycodo.sensors.chirp")


class ChirpSensor(AbstractSensor):
    """
    A sensor support class that measures the Chirp's moisture, temperature
    and light

    """

    def __init__(self, address, bus):
        super(ChirpSensor, self).__init__()
        self.address = address
        self.bus = smbus.SMBus(bus)
        self._lux = 0
        self._moisture = 0
        self._temperature = 0.0

    def __repr__(self):
        """  Representation of object """
        return "<{cls}(lux={lux})(moisture={moist})(temperature={temp})>".format(
            cls=type(self).__name__,
            lux="{0}".format(self._lux),
            moist="{0}".format(self._moisture),
            temp="{0:.2f}".format(self._temperature))

    def __str__(self):
        """ Return measurement information """
        return "Light: {lux}, Moisture: {moist}, Temperature: {temp}".format(
            lux="{0}".format(self._lux),
            moist="{0}".format(self._moisture),
            temp="{0:.2f}".format(self._temperature))

    def __iter__(self):  # must return an iterator
        """ ChirpSensor iterates through live measurement readings """
        return self

    def next(self):
        """ Get next measurement reading """
        if self.read():  # raised an error
            raise StopIteration  # required
        return dict(lux=float('{0}'.format(self._lux)),
                    moisture=float('{0}'.format(self._moisture)),
                    temperature=float('{0:.2f}'.format(self._temperature)))

    @property
    def lux(self):
        """ Chirp light measurement """
        if not self._lux:  # update if needed
            self.read()
        return self._lux

    @property
    def moisture(self):
        """ Chirp moisture measurement """
        if not self._moisture:  # update if needed
            self.read()
        return self._moisture

    @property
    def temperature(self):
        """ Chirp temperature in Celsius """
        if not self._temperature:  # update if needed
            self.read()
        return self._temperature

    def get_measurement(self):
        """ Gets the light, moisture, and temperature """
        lux = self.light()
        moisture = self.moist()
        temperature = self.temp() / 10.0
        return lux, moisture, temperature

    def read(self):
        """
        Takes a reading from the AM2315 and updates the self.dew_point,
        self._humidity, and self._temperature values

        :returns: None on success or 1 on error
        """
        try:
            self._lux, self._moisture, self._temperature = self.get_measurement()
            return  # success - no errors
        except Exception as e:
            logger.error("{cls} raised an exception when taking a reading: "
                         "{err}".format(cls=type(self).__name__, err=e))
        return 1

    def get_reg(self, reg):
        # read 2 bytes from register
        val = self.bus.read_word_data(self.address, reg)
        # return swapped bytes (they come in wrong order)
        return (val >> 8) + ((val & 0xFF) << 8)

    def reset(self):
        # To reset the sensor, write 6 to the device I2C address
        self.bus.write_byte(self.address, 6)

    def set_addr(self, new_addr):
        # To change the I2C address of the sensor, write a new address
        # (one byte [1..127]) to register 1; the new address will take effect after reset
        self.bus.write_byte_data(self.address, 1, new_addr)
        self.reset()
        # self.address = new_addr

    def moist(self):
        # To read soil moisture, read 2 bytes from register 0
        return self.get_reg(0)

    def temp(self):
        # To read temperature, read 2 bytes from register 5
        return self.get_reg(5)

    def light(self):
        # To read light level, start measurement by writing 3 to the
        # device I2C address, wait for 3 seconds, read 2 bytes from register 4
        self.bus.write_byte(self.address, 3)
        time.sleep(1.5)
        return self.get_reg(4)

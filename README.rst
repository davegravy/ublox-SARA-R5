======
U-blox R5
======

Python library for U-blox SARA-R5 modules.

Installation
============

Python version supported: 3.6+

.. code-block::

    pip install ubloxR5


About
=====

The ublox library gives a python interface to AT Commands via serial interface
to Ublox modules. This can used for testing and profiling of modules and
technologies or you might want to hook up a small python program on an embedded
device to send data over, for example, NB-IoT.

Supported Modules
=================

* SARA-R500S
* SARA-R510S

Example Use:
============

.. code-block::

    module = SaraR5Module(serial_port='/dev/tty.usbmodem14111')
    module.setup()
    module.connect(operator=24001)
    module.update_radio_statistics()
    print(module.radio_statistics["RSRQ"])

Documentation
=============
Full documentation can be found at TBD








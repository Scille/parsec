.. image:: docs/parsec_doc_logo.png
    :align: center


======
Parsec
======


.. image:: https://img.shields.io/azure-devops/tests/Scille/parsec/1/master.svg
    :target: https://dev.azure.com/Scille/parsec/_build?definitionId=1&_a=summary
    :alt: Azure DevOps tests

.. image:: https://codecov.io/gh/Scille/parsec-cloud/branch/master/graph/badge.svg
    :target: https://codecov.io/gh/Scille/parsec-cloud
    :alt: Code coverage

.. image:: https://pyup.io/repos/github/Scille/parsec-cloud/shield.svg
    :target: https://pyup.io/repos/github/Scille/parsec-cloud/
    :alt: Updates

.. image:: https://img.shields.io/pypi/v/parsec-cloud.svg
    :target: https://pypi.python.org/pypi/parsec-cloud
    :alt: Pypi Status

.. image:: https://readthedocs.org/projects/parsec-cloud/badge/?version=latest
    :target: http://parsec-cloud.readthedocs.io/en/latest/?badge=latest
    :alt: Documentation Status

.. image:: https://img.shields.io/badge/code%20style-black-000000.svg
    :target: https://github.com/ambv/black
    :alt: Code style: black


Homepage: https://parsec.cloud

Documentation: https://parsec-cloud.readthedocs.org.

Parsec allows you to easily share your work and data in the cloud in total
privacy thanks to end-to-end cryptographic security.


.. image:: docs/parsec_snapshot.png
    :align: center


Key features:

- Works as a virtual drive on you computer. You can access and modify all the data
  stored in Parsec with your regular softwares just like you would on your local
  hard-drive.
- Never lose any data. Synchronization with the remote server never destroy any
  data, hence you can browse data history and recover from any point in time.
- Client-side cryptographic security. Data and metadata are only visible by you
  and the ones you choose to share with.
- Cryptographic signature. Each modification is signed by it author making trivial
  to identify modifications.
- Cloud provider agnostic. Server provides connectors for S3 and swift object
  storage.
- Simplified enrollment. New user enrollment is simple as sharing a link and a token code.


Installation methods
====================

Windows installer
-----------------
Windows installers are available at https://github.com/Scille/parsec-cloud/releases/latest

Linux Snap
----------
Available for Linux through Snapcraft at https://snapcraft.io/parsec

Python PIP
----------
Parsec is also available directly through PIP for both Linux and Windows with Python > 3.6 with the command:
``pip install parsec-cloud``
(or, if you need to specify Python 3 pip version, ``pip3 install parsec-cloud``)

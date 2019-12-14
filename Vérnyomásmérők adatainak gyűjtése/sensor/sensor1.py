import logging
import os
import sys
import time
import datetime
import json
import random

import zmq
import shutil
import zmq.auth
from zmq.auth.thread import ThreadAuthenticator


''' Saját kliens kulcs generálása meglévő szerver kulcshoz (public_keys/server.key) '''


def generate_certificates(base_dir):
    keys_dir = os.path.join(base_dir, 'certificates')
    public_keys_dir = os.path.join(base_dir, 'public_keys')
    secret_keys_dir = os.path.join(base_dir, 'private_keys')

    # Create directories for certificates, remove old content if necessary
    for d in [keys_dir, public_keys_dir, secret_keys_dir]:
        if os.path.exists(d):
            for key_file in os.listdir(d):
                if os.path.exists(os.path.join(d, 'client%d.key_secret' % SERIAL_NUM)):
                    os.remove(os.path.join(d, 'client%d.key_secret' % SERIAL_NUM))
                if os.path.exists(os.path.join(d, 'client%d.key' % SERIAL_NUM)):
                    os.remove(os.path.join(d, 'client%d.key' % SERIAL_NUM))
        if not os.path.exists(d):
            os.mkdir(d)

    # create new keys in certificates dir
    client_public_file, client_secret_file = zmq.auth.create_certificates(keys_dir, "client%d" % SERIAL_NUM)

    # move public keys to appropriate directory
    for key_file in os.listdir(keys_dir):
        if key_file.endswith(".key"):
            shutil.move(os.path.join(keys_dir, key_file),
                        os.path.join(public_keys_dir, '.'))

    # move secret keys to appropriate directory
    for key_file in os.listdir(keys_dir):
        if key_file.endswith(".key_secret"):
            shutil.move(os.path.join(keys_dir, key_file),
                        os.path.join(secret_keys_dir, '.'))


'''Socket készítése és titkosítása'''


def get_socket (): #a socketet adja vissza
    client = ctx.socket(zmq.REQ)
    client.setsockopt(zmq.LINGER, 0)

    # We need two certificates, one for the client and one for
    # the server. The client must know the server's public key
    # to make a CURVE connection.
    client_secret_file = os.path.join(secret_keys_dir, "client%d.key_secret" % SERIAL_NUM)
    client_public, client_secret = zmq.auth.load_certificate(client_secret_file)
    client.curve_secretkey = client_secret
    client.curve_publickey = client_public

    # The client must know the server's public key to make a CURVE connection.
    server_public_file = os.path.join(public_keys_dir, "server.key")
    server_public, _ = zmq.auth.load_certificate(server_public_file)
    client.curve_serverkey = server_public

    return client


'''Konfigurációs adatok'''
endpoint = "tcp://127.0.0.1:5555"
endpoint_ip = '127.0.0.1'
SERIAL_NUM = 414516
MAX_BUFFER = 10

if __name__ == '__main__':
    if zmq.zmq_version_info() < (4,0):
        raise RuntimeError("Security is not supported in libzmq version < 4.0. libzmq version {0}".format(zmq.zmq_version()))
    generate_certificates(os.path.dirname(__file__))

# These directories are generated by the generate_certificates script
base_dir = os.path.dirname(__file__)
keys_dir = os.path.join(base_dir, 'certificates')
public_keys_dir = os.path.join(base_dir, 'public_keys')
secret_keys_dir = os.path.join(base_dir, 'private_keys')

if not (os.path.exists(keys_dir) and
        os.path.exists(public_keys_dir) and
        os.path.exists(secret_keys_dir)):
    print("Certificates are missing: run generate_certificates.py script first")
    sys.exit(1)
try:
    ctx = zmq.Context.instance()

    # Start an authenticator for this context.
    auth = ThreadAuthenticator(ctx)
    auth.start()
    auth.allow(endpoint_ip)
    # Tell the authenticator how to handle CURVE requests
    auth.configure_curve(domain='*', location=zmq.auth.CURVE_ALLOW_ANY)

    client = get_socket()
    poller = zmq.Poller()
    poller.register(client, zmq.POLLIN)  # POLLIN for recv, POLLOUT for send

    client.connect(endpoint)
    print("bound to zmq endpoint", endpoint)

except:
    print("creating zmq context/authentication failed")
    quit()


'''Végtelen ciklus kezdete'''
packed = {'measurements': []}
try:
    while True:
        time.sleep(2.2)
        systolic_pres = random.randint(90, 140)
        diastolic_pres = random.randint(60, 90)
        ts = time.time()
        timestamp = datetime.datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S')

        packed['measurements'].append({
            'serial_num': SERIAL_NUM, #gyári szám (konstans)
            'systolic_pres': systolic_pres, #egyik mérési adat
            'diastolic_pres': diastolic_pres, #másik mérési adat
            'time': timestamp, #időbélyeg
            'buffer_pos': len(packed['measurements'])+1 #puffer pozíció a lista hosszából
        })

        sent = False
        try:
            print("\nSending %d message(s) from buffer..." %
                  len(packed['measurements']))
            client.send_json(packed)
            sent = True
        except:
            print("error sending message")
            if len(packed['measurements']) >= MAX_BUFFER:
                print('- buffer maxed out (%d item)\n- dropping oldest measurement (%d already dropped)' % (
                    MAX_BUFFER,
                    packed['measurements'][0]['buffer_pos']-1))
                packed['measurements'].pop(0)
            try:
                print("reconnecting server...")
                client.close()
                client = get_socket()
                poller = zmq.Poller()
                poller.register(client, zmq.POLLIN)
                client.connect(endpoint)
            except:
                print("creating zmq context/authentication failed")
                quit()

        if sent:
            arrived = False
            last_received_id = -1

            socks = dict(poller.poll(2000))  # POLL socket for 2000ms
            if client in socks:
                try:
                    last_received_id = client.recv()
                    if int(last_received_id) == len(packed['measurements']):
                        arrived = True
                except:
                    print("error reading respond message to #%d" %
                          packed['measurements'][len(packed['measurements'])-1]['buffer_pos'])

            if arrived:
                print('Server received: %d measurement(s) from %d sensor buffer' % (
                    packed['measurements'][len(packed['measurements'])-1]['buffer_pos'],
                    packed['measurements'][len(packed['measurements'])-1]['serial_num']))
                packed = {'measurements': []}
            else:
                if int(last_received_id) == 0:
                    print('Server DID NOT save: %d measurement(s) from %d sensor buffer' % (
                        packed['measurements'][len(packed['measurements']) - 1]['buffer_pos'],
                        packed['measurements'][len(packed['measurements']) - 1]['serial_num']))
                else:
                    print('Server DID NOT received: %d measurement(s) from %d sensor buffer' % (
                        packed['measurements'][len(packed['measurements'])-1]['buffer_pos'],
                        packed['measurements'][len(packed['measurements'])-1]['serial_num']))
                if len(packed['measurements']) >= MAX_BUFFER:
                    print('- buffer maxed out (%d item)\n- dropping oldest measurement (%d already dropped)' % (
                        MAX_BUFFER,
                        packed['measurements'][0]['buffer_pos'] - 1))
                    packed['measurements'].pop(0)
                try:
                    print("reconnecting server...")
                    client.close()
                    client = get_socket()
                    poller = zmq.Poller()
                    poller.register(client, zmq.POLLIN)
                    client.connect(endpoint)
                except:
                    print("creating zmq context/authentication failed")
                    quit()


except KeyboardInterrupt:
    print('Stopping client...')
    # stop auth thread
    auth.stop()

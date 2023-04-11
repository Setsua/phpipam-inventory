#!/usr/bin/env python3
import os
import argparse
import requests
import time
try:
    import json
except ImportError:
    import simplejson as json
class PhpipamInventory(object):
    def __init__(self):
        # Init vars from env
        self.inventory = {
            '_meta': {
            }
        }
        self.read_cli_args()
        self.api_url = os.getenv('IPAM_ADDR')
        self.api_user = os.getenv("IPAM_API_USER")
        self.user = os.getenv("IPAM_USER")
        self.password = os.getenv("IPAM_PASS")
        self.cache_expiration_time = int(os.getenv('IPAM_CACHE', 600))

        # Cache related
        cache_path = '/tmp'
        if not os.path.exists(cache_path):
            os.makedirs(cache_path)
        self.cache_file = os.path.join(cache_path, "ansible-inventory.cache")

        # Called with `--list`.
        if self.args.list:
            if self.is_cache_stale():
                self.phpipam_inventory()
                with open(self.cache_file, 'w') as f:
                    f.write(json.dumps(self.inventory))
            else:
                with open(self.cache_file, 'r') as f:
                    cache_file_data = f.read()
                    self.inventory = json.loads(cache_file_data)

        # Called with `--host [hostname]`.
        elif self.args.host:
            # Not implemented, since we return _meta info `--list`.
            self.inventory = self.empty_inventory()

        # If no groups or vars are present, return an empty inventory.
        else:
            self.inventory = self.empty_inventory()
        print(json.dumps(self.inventory))

    # Main function for add hosts or create groups
    def phpipam_inventory(self):
        addresses_data = self.get_addresses()
        locations_data = self.get_location()
        MAINTENANCE = "5"

        # Add all intances to inventory
        for dc in locations_data:
            self.inventory[dc['name'].lower()] = {
                "children": []
            }

        for address in addresses_data:
            self.inventory[address['custom_criticality'].lower()] = {
                "children": []
            }

            #Add hosts or create groups
            if address['custom_group'] and address['location'] and int(address['location']) != 0:
                location = next(
                    (loc for loc in locations_data if loc['id'] == address['location']), None)

                # add ip address to host vars (ansible_host)
                self.add_ip(address['hostname'], address['ip'])

                # Add online state to host vars
                if address['tag'] == MAINTENANCE:
                    self.add_online_state(address['hostname'], 1)
                else:
                    self.add_online_state(address['hostname'], self.get_online_state(address['id']))

                # Add groups with location suffix
                self.add_group_suffix(address, location['name'].lower())

                # Add groups with criticality suffix
                self.add_group_suffix(address, address['custom_criticality'].lower())

                # Add dns cname to hostvars 
                if address['custom_cname']:
                    self.add_cname(address['hostname'], address['custom_cname'])

                # Add dns alias to hostvars
                if address['custom_service']:
                    self.add_service(address['hostname'], address['custom_service'])

    # Empty inventory.
    def empty_inventory(self):
        return {'_meta': {'hostvars': {}}}

    # Read the command line args passed to the script.
    def read_cli_args(self):
        parser = argparse.ArgumentParser()
        parser.add_argument('--list', action='store_true')
        parser.add_argument('--host', action='store')
        parser.add_argument('--refresh', action='store_true',
                            help='Refresh cached information')
        self.args = parser.parse_args()

    # Determines if cache file has expired, or if it is still valid
    def is_cache_stale(self):
        refresh = self.args.refresh
        cache_file = self.cache_file
        cache_expiration_time = self.cache_expiration_time
        if refresh:
            return True
        if os.path.isfile(cache_file) and os.path.getsize(cache_file) > 0:
            mod_time = os.path.getmtime(cache_file)
            current_time = time.time()
            if (mod_time + cache_expiration_time) > current_time:
                return False
        return True

    # Authentication to phpIPAM API
    def auth(self):
        session = requests.Session()
        session.auth = (self.user, self.password)
        r = session.post(self.api_url + self.api_user + "/user/")
        data = r.json()
        return data['data']['token']

    # Get all addresses from phpIPAM section addresses
    def get_addresses(self):
        response = requests.get(
            self.api_url + self.api_user + '/addresses/',
            headers={'token': self.auth()}
        )
        return response.json()["data"]

    # Get all location from phpIPAM section tools locations
    def get_location(self):
        response = requests.get(
            self.api_url + self.api_user + '/tools/locations/',
            headers={'token': self.auth()}
        )
        return response.json()["data"]

    # Get online state for each host
    def get_online_state(self, id):
        response = requests.get(
            self.api_url + self.api_user + '/addresses/' + id + '/ping/',
            headers={'token': self.auth()}
        )
        return response.json()["data"]['exit_code']

    # add group suffix and add group
    def add_group_suffix(self, address, suffix):
        group_name = address['custom_group'] + \
            '_' + suffix

        if not group_name in self.inventory:
            self.add_group(
                suffix, address['custom_group'])

        self.add_host(group_name, address['hostname'])

        if address['custom_parent']:
            if address['custom_parent'] + '_' + suffix not in self.inventory:
                self.add_group(
                    suffix, address['custom_parent'], has_parent=True)

            self.inventory[address['custom_parent'] + '_' + suffix]['children'].append(
                    address['custom_group'] + '_' + suffix)            

    # Add group function
    def add_group(self, location_name, group_name, has_parent=False):
        # Create hosts group
        self.inventory[group_name + '_' + location_name] = {
            "children": [],
            "hosts": []
        }

        # Create or Add parent group
        if group_name in self.inventory:
            self.inventory[group_name]['children'].append(
                group_name + '_' + location_name)
        else:
            self.inventory[group_name] = {
                "children": [group_name + '_' + location_name]
            }

        # Add to intance group
        self.inventory[location_name]['children'].append(
            group_name + '_' + location_name)

    # Add host function
    def add_host(self, group_name, hostname):
        self.inventory[group_name]['hosts'].append(hostname)

    # Add ip function
    def add_ip(self, hostname, ip):  
        if not "hostvars" in self.inventory['_meta']:
            self.inventory['_meta'] = {
                "hostvars": {}
            }

        if not hostname in self.inventory['_meta']['hostvars']:
            self.inventory['_meta']['hostvars'].update({
                hostname: {}
            })

        self.inventory['_meta']['hostvars'][hostname].update({
                    "ansible_host": ip
                })

    # Add online state to hostvars
    def add_online_state(self, hostname, state):
        if state == 0:
            self.inventory['_meta']['hostvars'][hostname].update({
                "status": {
                    "enabled": True
                }
            })
        else:
            self.inventory['_meta']['hostvars'][hostname].update({
                "status": {
                    "enabled": False
                }
            })

    # Add cname to hostvars
    def add_cname(self, hostname, cname):
        self.inventory['_meta']['hostvars'][hostname].update({
            "cname": cname 
        })

    # Add alternative service names for response policy zone
    def add_service(self, hostname, service):
        self.inventory['_meta']['hostvars'][hostname].update({
            "service": service
        })

# Get the inventory
if __name__ == '__main__':
    PhpipamInventory()

from netmiko import ConnectHandler, NetmikoAuthenticationException
from getpass import getpass
from netaddr import *
import csv
import time

# Gather the login credentials for sequentially connecting to the IPs provided
print('','Please enter AAA credentials below:',sep='\n')
un = input('Username: ')
pw = getpass()
core_sw_ip = input('Please enter core switch IP address: ')
print('')

# Create dictionaries for use for storage
core_mac_addr_table = {}
provided_mac_addr = []
ports_list = []
ip_addr = []
not_located_macs = []

# Gather the list of MAC addresses to search for
with open('mac_addresses.csv', 'r') as devices:

    # Format MAC addresses supplied to match cisco's xxxx.xxxx.xxxx format
    print('-' * 80)
    for d in devices:
        try:
            d = EUI(d)
            d.dialect = mac_cisco
            provided_mac_addr.append(str(d))
        except:
            print(d + 'appears to be an invalid MAC address')
            continue
    print('-' * 80)
    
    # Wrapping CLI commands in try loop to allow for SSH exception handling
    try:
        # Initiate SSH connection
        ssh_con = {'device_type':'cisco_ios',
                'host':core_sw_ip,
                'username':un,
                'password':pw,
                }
        
        # Connect to the core and retrive it's MAC address table
        print('')
        print('Connecting to provided core switch IP to retrieve MAC address table')
        net_conn = ConnectHandler(**ssh_con)
        core_sw_host = net_conn.find_prompt().strip('#')
        print('Sucessfully connected to: ' + core_sw_host)
        print('')
        mac_entries = net_conn.send_command('show mac address-table', use_textfsm=True)
        print('Retrieved MAC address table from core switch')
        print('')
        print('-' * 80)

        # Gather the etherchannel summary table to reference for interfaces
        ether_summ = net_conn.send_command(('show etherchannel summary'), use_textfsm=True)

        # Gather CDP Neighbor information to get current IP address connected to interface
        cdp_neighbors = net_conn.send_command(('show cdp neighbor detail'), use_textfsm=True)
    
    except Exception as e:
        print(e)
        time.sleep(3)
        exit()

    except NetmikoAuthenticationException as ssh_e:
        print(ssh_e)
        time.sleep(1)
        exit()

    # Create mac address:port key value pair in mac_addr_table so that mac address can be searched directly as the key to find the port
    for i in mac_entries:
        core_mac_addr_table[(i['destination_address'])] = i['destination_port']
    
    # Store the ports that the supplied mac addresses are on
    for m in provided_mac_addr:
        try:
            ports_list.append(core_mac_addr_table[m])
        except:
            print(m, 'was not located')
            not_located_macs.append(m)
    print('-' * 80)

    # Remove duplicate values from the list
    ports_list = list(set(ports_list))

    # Identify specific interface that connects to switch and get IP address for the switch
    interf = []
    for p in ports_list:
        for e in ether_summ:
            if e['po_name'] == p:
                if e['interfaces_status'][0] == 'P':
                    interf.append(e['interfaces'][0])
                elif e['interfaces_status'][0] == 'D':
                    interf.append(e['interfaces'][1])
                else:
                    print('Unable to resolve Port Channel into specific interfaces')

        # Etherchannel summary returns 'Te' instead of TenGigabitEthernet, which CDP neighbors utilizes instead, add IP to list
        for n in interf:
            n = n.replace('Te','TenGigabitEthernet')
            for c in cdp_neighbors:
                if c['local_port'] == n:
                    ip_addr.append(c['management_ip'])

# Create lists to reference which items were successfully located and which weren't
located_macs = []
not_located_macs = []
target_interface_dict = []

# Provide user feedback that script is progressing
print('Identified that provided MAC addresses reside on the following switches:')
for ips in ip_addr:
    print(ips)
print('-' * 80)
print('Connecting to switches to identify interfaces')
print('-' * 80)

# Connect to each switch IP that's been gathered
for ip in ip_addr:
    ssh_con = {'device_type':'cisco_ios',
            'host':ip,
            'username':un,
            'password':pw,
            }
    net_conn = ConnectHandler(**ssh_con)
    sw_host = net_conn.find_prompt().strip('#')
    print('Sucessfully connected to: ' + sw_host, ' | ', ip)
    print('')
    mac_entries = net_conn.send_command('show mac address-table', use_textfsm=True)
    for m in provided_mac_addr:
        for e in mac_entries:
            # If the provided mac is in this mac address table then the script will store the MAC, switch IP, switchport, and current vlan into a dictionary
            if e['destination_address'] == m:
                target_interface_dict.append({
                'device':m,
                'switch_ip':ip,
                'switch_hostname':sw_host,
                'switchport':(e['destination_port']),
                'current_vlan':(e['vlan'])
                })
                located_macs.append(m)
                print(m, 'located on',(e['destination_port']))
            # If the provided mac isn't in this table then it will append to the list
            else:
                not_located_macs.append(m)
    print('-' * 80)

# Provide feedback and ask for input from the user on whether they want to change the vlans of the interfaces supplied or not
print('Successfully located', len(target_interface_dict), 'end devices on', len(ip_addr), 'different switches')
if len(target_interface_dict) == 0:
    print('')
    print('No MAC addresses located, please check the mac_addresses.csv file')
    time.sleep(1)
    print('')
    print('Closing script session')
    time.sleep(3)
    exit()
print('')
while True:
    proceed_with_config = input('Would you like to proceed with changing the VLAN for these devices? (y/n): ').lower()
    if proceed_with_config == 'y':
        print('Proceeding with changing VLAN assignment')
        break
    elif proceed_with_config == 'n':
        print('Exiting script')
        exit()
    else:
        print('Invalid input please enter y or n')
print('-' * 80)

# Connect to the end switches and change the VLAN assignment on the previously identified ports
vlan_choice = None
while True:
    vlan_choice = input('Please enter the number of the VLAN you wish to assign to these devices: ')
    confirm = input("You've chosen VLAN " + vlan_choice + ' is this correct? (y/n): ').lower()
    if confirm == 'y':
        print('Assigning VLAN', vlan_choice, 'to device interfaces')
        break
    if confirm == 'n':
        print('')
print('-' * 80)

try:
    correct_vlan = []
    incorrect_vlan = []
    second_switch_connection = []
    
    # Identify if the device needs to be changed or not first to avoid unnecessary SSH connections
    for t in target_interface_dict:
        if t['current_vlan'] == vlan_choice:
            correct_vlan.append(t['device'])
        else:
            incorrect_vlan.append({
                'mac':(t['device']),
                'ip':(t['switch_ip']),
                'interface':(t['switchport'])
            })
            second_switch_connection.append(t['switch_ip'])
    # Print a list of the items that are already on the correct vlan and don't need changed
    if len(correct_vlan) > 0:
        print('The following devices were already assigned to VLAN', vlan_choice + ':')
        for items in correct_vlan:
            print(items)
        print('')
    # Print the devices that are being changed, then log into the relevant switch and make the configuration change
    if len(incorrect_vlan) > 0:
        print('Assigning the following devices to VLAN', vlan_choice + ':')

        # Connect to the switch 1 time and make all changes, then move on to the next switch
        second_switch_connection = list(set(second_switch_connection))
        for ip in second_switch_connection:
            ssh_con = {'device_type':'cisco_ios',
                'host':ip,
                'username':un,
                'password':pw,
                'session_log': 'netmiko_session' + ip + '.txt'
                }
            net_conn = ConnectHandler(**ssh_con)
            for i in incorrect_vlan:
                if i['ip'] == ip:
                    print(i['mac'])
                    commands = [('interface ' + i['interface']),('switchport access vlan ' + vlan_choice)]
                    configure_vlan = net_conn.send_config_set(commands)
                    for t in target_interface_dict:
                        if t['device'] == i['mac']:
                            t['current_vlan'] = vlan_choice
                        else:
                            continue
                else:
                    continue

            # Save the switch config before moving onto the next switch
            print('Saving', ip, 'config')
            write_mem = net_conn.save_config()
            print('Config saved')
        print('Successfully changed the assigned VLAN for devices')
    print('All MAC addresses are now assigned to VLAN', vlan_choice)
    print('-' * 80)

    # Record information into .csv file for reference
    print('Storing results into .csv file')
    output_file = (input('Please enter a name for the file: ')+'.csv')
    with open(output_file, 'w', newline='') as f:
        fields = ['device', 'switch_hostname', 'switch_ip', 'switchport', 'current_vlan']
        w = csv.DictWriter(f, fieldnames=fields, extrasaction= 'ignore', dialect= 'excel')
        w.writeheader()
        for i in target_interface_dict:
            w.writerow(i)
    print('Saved results to', output_file)

# Exception handling to stop the script if SSH info is incorrect
except NetmikoAuthenticationException as ssh_e:
        print(ssh_e)
        exit()
except Exception as e:
        print(e)
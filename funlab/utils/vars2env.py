import argparse
import os
import sys
from pathlib import Path
import tomllib
from cryptography.fernet import Fernet, InvalidToken

def generate_key(key_name:str=None):
    """
    This function generates a key and saves it into environment variable named 'key_name'
    """
    if not key_name:
        key_name = key = Fernet.generate_key().decode()
        key_name=key_name.replace("=","")  # illegal char for env name
    key = Fernet.generate_key().decode()
    try:
        os.environ[key_name] = key
        print('A key is generated and set to environment variable as below, use the "Key Name" to pass to your config for "ENV" variable decryption.')
        print(f'Key Name = {key_name}, key={key}')
        return key_name
    except Exception as e:
        print('Set environ failed! Eception:'+ str(e))
        raise e

def encrypt_var_into_env(var_name:str, var_value: str, key_name: str)->str:
    """Encrypts the var's value and save as environment variable.

    Args:
        var_name (str): the variable's name
        var_value (str): the variable's value
        key_name (str): use the key_name to get key for decrypt from environment variable

    Returns:
        str: the encrypted value
    """
    # print(f'{var_name} = {var_value}')
    key = os.environ.get(key_name, None)
    encrypted_value = var_value.encode()
    f = Fernet(key.encode())
    encrypted_value = f.encrypt(encrypted_value).decode()
    os.environ[var_name] = encrypted_value
    return encrypted_value

def get_env_encrypt_var(var_name:str, key_name: str):
    """
    Decrypts the var_value
    """
    if not (key:=os.environ.get(key_name, None)):
        print(f"Warning:Can't get {key_name}'s key in environment varables, just retirm the raw one.")
        return os.environ[var_name].decode()
    f = Fernet(key.encode())
    try:
        decrypted_var = f.decrypt(os.environ[var_name].encode())
    except InvalidToken:
        print(f"Warning:{var_name}'s value is not encrypted, just retunr the raw one.")
        return os.environ[var_name].decode()
    return decrypted_var.decode()

def encode_envfile_vars(env_file, key_name:str=None):
    print(f"This program will encoding {env_file} for application's environment var_value protection.")
    # input("Press Enter to continue...")
    print(f'Encoding file: {env_file}')
    if Path(env_file).exists():
        with open(env_file, "rb") as f:
            vars = tomllib.load(f)
    else:
        raise Exception(f'Not found .env file:{env_file}, check!')
    key_name = generate_key(key_name)
    for var_name, var_value in vars.items():
        if isinstance(var_value, str):
            encrypted = encrypt_var_into_env(var_name=var_name, var_value=var_value, key_name=key_name)
            print(f'{var_name} is encryped') # : {encrypted}')
        else:
            raise Exception(f'Variable value only support string value, quote with "" or '', check:{var_name}')
    return key_name

def main(args=None):
    if not args:
        args = sys.argv[1:]
    parser = argparse.ArgumentParser(description="Encoding .env file for application's environment var_value protection. Programing by 013 ...")
    parser.add_argument("-e", "--envfile", dest="envfile", help="specify .env file name and path")
    parser.add_argument("-k", "--keyname", dest="keyname", default='', help="specify the keyname and use it to save the key into environment varable.")
    args = parser.parse_args(args)
    encode_envfile_vars(args.envfile, args.keyname)

import sys

if __name__ == "__main__":
    args = None
    args= ['-e', '.env'] # , '-k', '304929681ec4cf040877a7b8161b34e0']

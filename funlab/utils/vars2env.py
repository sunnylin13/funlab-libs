import argparse
import os
import sys
from pathlib import Path
import tomllib
from cryptography.fernet import Fernet, InvalidToken
import re

def validate_env_name(key_name, default_name='DEFAULT'):
    key_name = re.sub('[^a-zA-Z0-9_]', '', key_name)
    if key_name and key_name[0].isdigit():
        key_name = '_' + key_name
    if not key_name:
        return default_name
    return key_name

def generate_key(key_name:str=None):
    """
    This function generates a key and saves it into environment variable named 'key_name'
    """
    if not key_name:
        key_name = Fernet.generate_key().decode()
    key_name=validate_env_name(key_name)  # illegal char for env name
    key = Fernet.generate_key().decode()
    try:
        os.environ[key_name] = key
        # print('A key is generated and set to environment variable as below, use the "Key Name" to pass to your config for "ENV" variable decryption.')
        # print(f'Key Name = {key_name}, key={key}')
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
    key_name=validate_env_name(key_name)  # illegal char for env name
    key = os.environ.get(key_name, None)
    encrypted_value = var_value.encode()
    f = Fernet(key.encode())
    encrypted_value = f.encrypt(encrypted_value).decode()
    os.environ[var_name] = encrypted_value
    return encrypted_value

def get_env_var_value(var_name:str, key_name: str):
    """
    Decrypts the var_value
    """
    key_name=validate_env_name(key_name)  # illegal char for env name
    if not (key:=os.environ.get(key_name, None)):
        print(f"Warning:Can't get {key_name}'s key in environment varables, just return the raw one.")
        return os.environ[var_name]  # .decode()
    f = Fernet(key.encode())
    try:
        decrypted_var = f.decrypt(os.environ[var_name].encode())
    except InvalidToken:
        print(f"Warning:{var_name}'s value is not encrypted, just return the raw one.")
        return os.environ[var_name]  # .decode()
    return decrypted_var.decode()

def encode_envfile_vars(env_file, key_name:str=None):
    if Path(env_file).exists():
        with open(env_file, "rb") as f:
            vars = tomllib.load(f)
    else:
        raise Exception(f'Not found .env file:{env_file}, check!')
    key_name = generate_key(key_name)
    for var_name, var_value in vars.items():
        if isinstance(var_value, str):
            encrypted = encrypt_var_into_env(var_name=var_name, var_value=var_value, key_name=key_name)
            # print(f'{var_name} is encryped') # : {encrypted}')
        else:
            raise Exception(f'Variable value only support string value, quote with "" or '', check:{var_name}')
    return key_name

def main(args=None):
    """
    主要函數，用於處理命令列參數並執行環境變數編碼。

    Args:
        args: 命令列參數，如果為 None 則從 sys.argv 取得
    """
    if not args:
        args = sys.argv[1:]
    parser = argparse.ArgumentParser(description="編碼 .env 檔案以保護應用程式的環境變數值。程式設計：013 ...")
    parser.add_argument("-e", "--envfile", dest="envfile", help="指定 .env 檔案名稱和路徑")
    parser.add_argument("-k", "--keyname", dest="keyname", default='', help="指定金鑰名稱並用它將金鑰儲存到環境變數中。")
    args = parser.parse_args(args)
    print(f"此程式將編碼變數值到作業系統環境中，以保護應用程式的敏感資料。")
    input("按 Enter 鍵繼續...")
    print(f'正在編碼檔案: {args.envfile}')
    keyname = encode_envfile_vars(args.envfile, args.keyname)
    print(f"編碼完成，使用金鑰：'{keyname}' 來取得數值。")

if __name__ == "__main__":
    # 移除硬編碼的測試參數，改為使用實際的命令列參數
    main()
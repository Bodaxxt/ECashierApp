# config_manager.py

import json
import os

CONFIG_FILE = 'prices_config.json'

def get_default_prices():
    """
    Returns a dictionary with the original, hard-coded prices.
    This is used for initialization and resetting to defaults.
    """
    return {
        'USERS': {"admin": "1234", "ahmed": "5678"},
        'ORDER_STATUSES': [
            "تم الاستلام", "في مرحلة التصميم", "جاهز للطباعة", "جاري الطباعة",
            "في مرحلة التشطيب", "جاهز للتسليم", "تم التسليم"
        ],
        'PRINTING_PRICES': {
            'كوشيه 115':  {'وجه': 4.00, 'وجهين': 7.00}, 'كوشيه 130':  {'وجه': 4.25, 'وجهين': 7.25}, 'كوشيه 150':  {'وجه': 4.50, 'وجهين': 7.50},
            'كوشيه 170':  {'وجه': 4.75, 'وجهين': 7.75}, 'كوشيه 200':  {'وجه': 5.00, 'وجهين': 8.00}, 'كوشيه 250':  {'وجه': 5.50, 'وجهين': 8.50},
            'كوشيه 300':  {'وجه': 5.75, 'وجهين': 8.75}, 'كوشيه 350':  {'وجه': 6.50, 'وجهين': 9.50}, 
            'استيكر ورق': {'وجه': 8.00},
            'استيكر بلاستيك': {'وجه': 14.00}
        },
        'LAMINATION_PRICES': {'لا يوجد': 0, 'سلوفان وجه واحد': 1, 'سلوفان وجهين': 2},
        'TRIMMING_PRICES': {'لا يوجد': 0, 'تشريح 5': 5, 'تشريح 7': 7, 'تشريح 10': 10},
        'BINDING_OPTIONS': {
            'لا يوجد': 0, ' 5': 5, ' 7': 7, ' 10': 10, ' 3 ': 3, ' 5 ': 5, ' 7 ': 7,
            '3_': 3, '5_': 5, '7_': 7, '10_': 10, 'هارد كافر A5': 25, 'هارد كافر A4': 40, 'هارد كافر A3': 75,
        },
        'LAKTA_PRICES': [2.5, 3.0],
        'ID_CARD_PRICING': [
            [10, 20.00], [50, 10.00], [100, 7.00], [300, 6.00], 
            [500, 5.00], [1000, 4.00], [999999, 3.50] # Use a large number instead of inf for JSON compatibility
        ],
        'MIN_CUTTING_PRICE': 15,
        'PLAIN_PAPER_TYPES': ['ورق طبع 70 جرام', 'ورق طبع 80 جرام', 'ورق طبع 100 جرام'],
        'QUANTITY_THRESHOLD': 1000,
        'PLAIN_PAPER_PRICES': {
            'ورق طبع 70 جرام': {'A4': {'small': {'وجه': 0.45, 'وجهين': 0.65}, 'large': {'وجه': 0.30, 'وجهين': 0.40}}, 'A3': {'small': {'وجه': 0.85, 'وجهين': 1.25}, 'large': {'وجه': 0.70, 'وجهين': 0.95}}},
            'ورق طبع 80 جرام': {'A4': {'small': {'وجه': 0.50, 'وجهين': 0.70}, 'large': {'وجه': 0.40, 'وجهين': 0.60}}, 'A3': {'small': {'وجه': 1.00, 'وجهين': 1.50}, 'large': {'وجه': 0.80, 'وجهين': 1.10}}},
            'ورق طبع 100 جرام': {'A4': {'small': {'وجه': 0.55, 'وجهين': 0.75}, 'large': {'وجه': 0.50, 'وجهين': 0.65}}, 'A3': {'small': {'وجه': 1.20, 'وجهين': 1.70}, 'large': {'وجه': 1.00, 'وجهين': 1.30}}}
        },
        'LASER_PLAIN_PAPER_PRICES': {
            'ورق طبع 70 جرام': {'A4': {'وجه': 0.95, 'وجهين': 1.70}, 'A3': {'وجه': 1.95, 'وجهين': 3.45}},
            'ورق طبع 80 جرام': {'A4': {'وجه': 1.00, 'وجهين': 1.75}, 'A3': {'وجه': 2.00, 'وجهين': 3.50}},
            'ورق طبع 100 جرام': {'A4': {'وجه': 1.10, 'وجهين': 1.85}, 'A3': {'وجه': 2.20, 'وجهين': 3.70}}
        },
        'STAPLING_PRICING_A5': [
            [100, 1.5], [150, 2.0], [200, 2.5], [300, 3.0], [400, 4.0], 
            [500, 5.0], [600, 6.0], [700, 7.0], [999999, 8.0]
        ],
        'STAPLING_PRICING_A4': [
            [100, 2.0], [150, 2.5], [200, 3.0], [300, 4.0], [400, 5.0], 
            [500, 6.0], [600, 7.0], [700, 8.0], [999999, 9.0]
        ],
        'MENU_LAMINATION_PRICING': [
            [10,   {'A5': 10, 'A4': 15, 'A3': 30}],
            [100,  {'A5': 7,  'A4': 10, 'A3': 20}],
            [500,  {'A5': 5,  'A4': 8,  'A3': 15}],
            [999999, {'A5': 4, 'A4': 6, 'A3': 10}]
        ]
    }

# In config_manager.py, replace the whole function with this one:

def load_prices():
    """
    Loads prices from the JSON config file, filling in missing keys from defaults.
    """
    defaults = get_default_prices()
    
    # If the file doesn't exist, create it with defaults and return
    if not os.path.exists(CONFIG_FILE):
        print("Config file not found. Creating with default values.")
        save_prices(defaults)
        return defaults
        
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            loaded_config = json.load(f)
            
        # <<<--- هذا هو الكود الجديد والمهم الذي يحل المشكلة --- >>>
        # It checks for any keys missing in the user's file and adds them.
        config_updated = False
        for key, value in defaults.items():
            if key not in loaded_config:
                print(f"Updating config: Adding missing key '{key}'...")
                loaded_config[key] = value
                config_updated = True
        
        # If we added new keys, save the updated file back.
        if config_updated:
            save_prices(loaded_config)
            
        # The logic to handle 'infinity' remains the same
        for key in ['ID_CARD_PRICING', 'STAPLING_PRICING_A5', 'STAPLING_PRICING_A4']:
            if key in loaded_config:
                for item in loaded_config[key]:
                    if isinstance(item[0], (int, float)) and item[0] >= 999999:
                         item[0] = float('inf')
        
        if 'MENU_LAMINATION_PRICING' in loaded_config:
            for item in loaded_config['MENU_LAMINATION_PRICING']:
                if isinstance(item[0], (int, float)) and item[0] >= 999999:
                    item[0] = float('inf')

        return loaded_config
            
    except (json.JSONDecodeError, IOError) as e:
        print(f"Error loading config file: {e}. Loading default values.")
        return get_default_prices()


def save_prices(prices_data):
    """Saves the provided prices dictionary to the config file."""
    try:
        # Important: Replace infinity with a large number before saving to JSON
        data_to_save = prices_data.copy()
        for key in ['ID_CARD_PRICING', 'STAPLING_PRICING_A5', 'STAPLING_PRICING_A4', 'MENU_LAMINATION_PRICING']:
            if key in data_to_save:
                # Create a new list to avoid modifying the list while iterating
                new_list = []
                for item in data_to_save[key]:
                    new_item = list(item) # create a mutable copy
                    if new_item[0] == float('inf'):
                        new_item[0] = 999999
                    new_list.append(new_item)
                data_to_save[key] = new_list

        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(data_to_save, f, ensure_ascii=False, indent=4)
    except IOError as e:
        print(f"Error saving config file: {e}")
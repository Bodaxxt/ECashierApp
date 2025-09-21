import customtkinter as ctk
from tkinter import messagebox, simpledialog, ttk
from datetime import datetime, date, timedelta
import sqlite3
import os
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import re
import calendar
from PIL import Image, ImageDraw, ImageFont
import sys
import config_manager
import arabic_reshaper
from bidi.algorithm import get_display
from fpdf import FPDF


# ==============================================================================
# 1. إعداد قاعدة البيانات <<<--- تم التعديل هنا --- >>>
# ==============================================================================
def init_database():
    conn = sqlite3.connect('receipts.db')
    cursor = conn.cursor()
    
    # جدول العملاء
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS customers (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, phone TEXT UNIQUE NOT NULL, notes TEXT
        )
    ''')
    
    # جدول الفواتير
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS receipts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp DATETIME NOT NULL, receipt_data TEXT NOT NULL,
            total_amount REAL NOT NULL, customer_id INTEGER, status TEXT, due_date TEXT, notes TEXT,
            discount REAL, amount_paid REAL, remaining_amount REAL,
            FOREIGN KEY (customer_id) REFERENCES customers (id)
        )
    ''')

    # جدول المصروفات
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp DATETIME NOT NULL,
            description TEXT NOT NULL, amount REAL NOT NULL
        )
    ''')
    
    # جدول المخزون
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            unit TEXT NOT NULL,
            stock_level REAL NOT NULL DEFAULT 0,
            low_stock_threshold REAL DEFAULT 10,
            purchase_price REAL NOT NULL DEFAULT 0  -- <<<--- تعديل: إضافة سعر الشراء
        )
    ''')

    # <<<--- تعديل: إضافة جدول لربط المواد بالفواتير --- >>>
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS job_materials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            receipt_id INTEGER NOT NULL,
            inventory_id INTEGER NOT NULL,
            quantity_used REAL NOT NULL,
            FOREIGN KEY (receipt_id) REFERENCES receipts (id),
            FOREIGN KEY (inventory_id) REFERENCES inventory (id)
        )
    ''')

    # التأكد من وجود الأعمدة الجديدة في الجداول القديمة
    try:
        cursor.execute("PRAGMA table_info(inventory)")
        cols = [col[1] for col in cursor.fetchall()]
        if 'purchase_price' not in cols:
            cursor.execute("ALTER TABLE inventory ADD COLUMN purchase_price REAL NOT NULL DEFAULT 0")
    except sqlite3.OperationalError as e:
        print(f"Could not update inventory table: {e}")
    
    conn.commit()
    conn.close()


# ==============================================================================
# 2. البيانات والأسعار (بدون تغيير)
# ==============================================================================
CONFIG = config_manager.load_prices()
USERS = CONFIG['USERS']
ORDER_STATUSES = CONFIG['ORDER_STATUSES']
PRINTING_PRICES = CONFIG['PRINTING_PRICES']
LAMINATION_PRICES = CONFIG['LAMINATION_PRICES']
TRIMMING_PRICES = CONFIG['TRIMMING_PRICES']
BINDING_OPTIONS = CONFIG['BINDING_OPTIONS']
ID_CARD_PRICING = [[limit if limit < 999999 else float('inf'), price] for limit, price in CONFIG['ID_CARD_PRICING']]
MIN_CUTTING_PRICE = CONFIG['MIN_CUTTING_PRICE']
PLAIN_PAPER_TYPES = CONFIG['PLAIN_PAPER_TYPES']
QUANTITY_THRESHOLD = CONFIG['QUANTITY_THRESHOLD']
PLAIN_PAPER_PRICES = CONFIG['PLAIN_PAPER_PRICES']
LASER_PLAIN_PAPER_PRICES = CONFIG['LASER_PLAIN_PAPER_PRICES']
STAPLING_PRICING_A5 = [[limit if limit < 999999 else float('inf'), price] for limit, price in CONFIG['STAPLING_PRICING_A5']]
STAPLING_PRICING_A4 = [[limit if limit < 999999 else float('inf'), price] for limit, price in CONFIG['STAPLING_PRICING_A4']]
MENU_LAMINATION_PRICING = [[limit if limit < 999999 else float('inf'), prices] for limit, prices in CONFIG['MENU_LAMINATION_PRICING']]
LAKTA_PRICES = CONFIG['LAKTA_PRICES']

# ==============================================================================
# 3. دوال مساعدة (بدون تغيير)
# ==============================================================================
def reshape_arabic(text):
    reshaped_text = arabic_reshaper.reshape(text)
    bidi_text = get_display(reshaped_text)
    return bidi_text

def clean_description(text):
    text = convert_numbers_to_hindi(text)
    text = re.sub(r'[a-zA-Z]', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def convert_numbers_to_hindi(text):
    mapping = {
        '0': '٠', '1': '١', '2': '٢', '3': '٣', '4': '٤',
        '5': '٥', '6': '٦', '7': '٧', '8': '٨', '9': '٩',
        '.': '٫' 
    }
    return "".join([mapping.get(char, char) for char in str(text)])

# ==============================================================================
# 4. دوال الفواتير (بدون تغيير جوهري)
# ==============================================================================
# ... (دوال format_receipt_for_display, generate_pdf_receipt, etc. تبقى كما هي) ...
# (الكود الخاص بها طويل لذا تم اختصاره هنا، لكن لا تحتاج لتغييره)
# <<<--- FIX 1: Add 'customer_phone=None' to the function definition --- >>>
def format_receipt_for_display(receipt_id, customer_name, timestamp, items, subtotal, discount, paid, remaining, notes, due_date, customer_phone=None):
    # <<<--- إضافة جديدة لاستخدامها في تقسيم النص الطويل --->>>
    import textwrap

    # --- 1. تحديد عرض الفاتورة والأعمدة ---
    RECEIPT_WIDTH = 48  # العرض الكلي للفاتورة (عدد الحروف) - مناسب للطابعات الحرارية
    COL_TOTAL = 10      # عرض عمود الإجمالي
    COL_PRICE = 9       # عرض عمود السعر
    COL_QTY = 6         # عرض عمود الكمية
    # عرض عمود الصنف يتم حسابه تلقائياً
    COL_DESC = RECEIPT_WIDTH - COL_TOTAL - COL_PRICE - COL_QTY - 3 # نطرح 3 للمسافات

    # --- 2. دالة مساعدة لبناء كل صف في الجدول (بترتيب معكوس) ---
    def build_row(desc, qty, price, total):
        # استخدم textwrap لتقسيم الوصف الطويل إلى قائمة من الأسطر
        desc_lines = textwrap.wrap(str(desc), width=COL_DESC)
        
        # تحويل الأرقام إلى الشكل الهندي
        qty_hindi = convert_numbers_to_hindi(str(qty))
        price_hindi = convert_numbers_to_hindi(str(price))
        total_hindi = convert_numbers_to_hindi(str(total))
        
        # بناء السطر الأول بالترتيب المعكوس (الإجمالي أولاً في النص)
        # سيظهر هذا الترتيب صحيحاً (الصنف على اليمين) في واجهة تدعم العربية
        first_line = (
            f"{total_hindi.rjust(COL_TOTAL)} "
            f"{price_hindi.rjust(COL_PRICE)} "
            f"{qty_hindi.center(COL_QTY)} "
            f"{(desc_lines[0] if desc_lines else '').rjust(COL_DESC)}"
        )
        
        row_output = [first_line]
        
        # إضافة بقية أسطر الوصف (إذا وجدت) تحت عامود الصنف
        for line in desc_lines[1:]:
            # نضيف مسافات فارغة مكان الأعمدة الأخرى
            left_padding = " " * (COL_TOTAL + COL_PRICE + COL_QTY + 3)
            row_output.append(f"{left_padding}{line.rjust(COL_DESC)}")
            
        return row_output

    # --- 3. بناء الفاتورة سطراً بسطر ---
    receipt_lines = []
    receipt_lines.append("التوفيق".center(RECEIPT_WIDTH))
    receipt_lines.append("تليفون: 01080324634".center(RECEIPT_WIDTH))
    receipt_lines.append("خاتم المرسلين- 7ج عمارات بنك مصر".center(RECEIPT_WIDTH))
    receipt_lines.append("=" * RECEIPT_WIDTH)
    
    date_str = convert_numbers_to_hindi(timestamp.strftime('%Y-%m-%d'))
    receipt_id_hindi = convert_numbers_to_hindi(receipt_id)
    # تنسيق رأس الفاتورة ليكون التاريخ على اليسار ورقم الفاتورة على اليمين
    padding = RECEIPT_WIDTH - len(f"فاتورة رقم: {receipt_id_hindi}") - len(date_str)
    header_info = f"{date_str}{' ' * padding}فاتورة رقم: {receipt_id_hindi}"
    receipt_lines.append(header_info)
    
    receipt_lines.append(f"العميل: {customer_name}")
    # <<<--- FIX 2: Add the customer's phone number to the receipt if it exists --- >>>
    if customer_phone:
        receipt_lines.append(f"تليفون: {convert_numbers_to_hindi(customer_phone)}")

    if due_date and due_date.strip():
        receipt_lines.append(f"تاريخ التسليم: {convert_numbers_to_hindi(due_date)}")
    receipt_lines.append("=" * RECEIPT_WIDTH)

    # إضافة عناوين الجدول بالترتيب الصحيح
    receipt_lines.extend(build_row('الصنف', '     الكمية   ', 'السعر', 'الإجمالي'))
    receipt_lines.append("--" * RECEIPT_WIDTH)

    total_from_items = sum(item.get('subtotal', 0) for item in items)
    # إضافة الأصناف إلى الجدول
    for item in items:
        receipt_lines.extend(build_row(
            item.get('description', ''),
            item.get('quantity', 1),
            f"{item.get('unit_price', 0):.2f}",
            f"{item.get('subtotal', 0):.2f}"
        ))
    
    receipt_lines.append("=" * RECEIPT_WIDTH)
    
    # --- 4. بناء ملخص الفاتورة النهائي (معكوس أيضاً) ---
    def format_summary_line(label, value_str):
        hindi_value = convert_numbers_to_hindi(value_str)
        # حساب المسافات اللازمة للمحاذاة
        padding = RECEIPT_WIDTH - len(label) - len(hindi_value)
        # وضع القيمة على اليسار والعنوان على اليمين
        return f"{hindi_value}{' ' * padding}{label}"

    receipt_lines.append(format_summary_line("الإجمالي قبل الخصم:", f"{total_from_items:.2f} ج.م"))
    receipt_lines.append(format_summary_line("الخصم:", f"{discount:.2f} ج.م"))
    receipt_lines.append(format_summary_line("الإجمالي بعد الخصم:", f"{(total_from_items - discount):.2f} ج.م"))
    receipt_lines.append(format_summary_line("المدفوع:", f"{paid:.2f} ج.م"))
    receipt_lines.append("--" * RECEIPT_WIDTH)
    receipt_lines.append(format_summary_line("المتبقي:", f"{remaining:.2f} ج.م"))
    receipt_lines.append("=" * RECEIPT_WIDTH)
    
    if notes and notes.strip():
        receipt_lines.append(":ملاحظات")
        # أيضاً نقوم بتقسيم الملاحظات الطويلة
        note_lines = textwrap.wrap(notes, width=RECEIPT_WIDTH, subsequent_indent="  ")
        receipt_lines.extend(note_lines)
        receipt_lines.append("--" * RECEIPT_WIDTH)
        
    receipt_lines.append("شكراً لتعاملكم معنا!".center(RECEIPT_WIDTH))
    
    return "\n".join(receipt_lines)
# ... The rest of the helper functions for printing and PDF generation remain the same
def convert_numbers(text):
    """
    تحول هذه الدالة الأرقام الإنجليزية (123) إلى أرقام عربية مشرقية (١٢٣)
    وتستبدل الفاصلة العشرية (.) بالفاصلة (,).
    """
    mapping = {
        '0': '٠', '1': '١', '2': '٢', '3': '٣', '4': '٤',
        '5': '٥', '6': '٦', '7': '٧', '8': '٨', '9': '٩',
        '.': ','
    }
    return "".join(mapping.get(c, c) for c in str(text))

def generate_pdf_receipt(filename, data):
    """
    تنشئ هذه الدالة فاتورة بصيغة PDF مع معالجة محسنة للغة العربية وبيانات الشركة الاختيارية.
    """
    if getattr(sys, 'frozen', False):
        application_path = os.path.dirname(sys.executable)
    else:
        application_path = os.path.dirname(os.path.abspath(__file__))

    logo_path = os.path.join(application_path, 'logo_black_transparent.png')
    font_path = os.path.join(application_path, 'arial.ttf')

    RECEIPT_WIDTH = 80
    RECEIPT_HEIGHT = 200
    MARGIN = 5
    
    pdf = FPDF('P', 'mm', (RECEIPT_WIDTH, RECEIPT_HEIGHT))

    try:
        pdf.add_font('Arial', '', font_path, uni=True) 
    except RuntimeError as e:
        print(
            "خطأ في الخطوط: لم يتم العثور على ملف خط arial.ttf.\n"
            "تأكد من وجوده في مجلد البرنامج."
        )
        return

    pdf.add_page()
    pdf.set_auto_page_break(True, margin=MARGIN)
    pdf.set_margins(MARGIN, MARGIN, MARGIN)
    pdf.set_font('Arial', '', 12)
    
    # --- معلومات الشركة (رأس الفاتورة) ---
    if os.path.exists(logo_path):
        pdf.image(logo_path, x=RECEIPT_WIDTH/2 - 15, y=2, w=30)
        pdf.ln(38)
    else:
        pdf.set_font('Arial', '', 20)
        pdf.cell(0, 10, get_display(arabic_reshaper.reshape("التوفيق")), 0, 1, 'C')
        pdf.set_font('Arial', '', 8)
        pdf.cell(0, 5, get_display(arabic_reshaper.reshape("للطباعة و الاعلان")), 0, 1, 'C')
        pdf.ln(5)
    
    phone = CONFIG.get('COMPANY_PHONE', '01080324634')
    address = CONFIG.get('COMPANY_ADDRESS', '7 ج خاتم المرسلين- خلف عمارات بنك مصر')

    pdf.set_font('Arial', '', 10)
    if phone:
        pdf.cell(0, 5, get_display(arabic_reshaper.reshape(f"تليفون: {phone}")), 0, 1, 'C')
    if address:
        pdf.cell(0, 5, get_display(arabic_reshaper.reshape(address)), 0, 1, 'C')
    
    dashed_line = "=" * 32
    pdf.ln(3)
    pdf.cell(0, 5, dashed_line, 0, 1, 'C')

    # --- معلومات الفاتورة ---
    invoice_date_ar = convert_numbers(data.get('timestamp', datetime.now()).strftime('%Y-%m-%d'))
    invoice_id_ar = convert_numbers(data.get('receipt_id', ''))
    reshaped_invoice_id_label = get_display(arabic_reshaper.reshape("فاتورة رقم: "))
    
    pdf.cell(pdf.get_string_width(invoice_date_ar) + 2, 8, invoice_date_ar, 0, 0, 'L')
    pdf.cell(0, 8, f"{reshaped_invoice_id_label}{invoice_id_ar}", 0, 1, 'R')
    
    reshaped_customer_label = get_display(arabic_reshaper.reshape("العميل: "))
    customer_name = get_display(arabic_reshaper.reshape(data.get('customer_name', '')))
    pdf.cell(0, 8, f"{customer_name} {reshaped_customer_label}", 0, 1, 'R')

    # <<<--- التعديل هنا: إضافة رقم هاتف العميل إذا كان موجوداً --- >>>
    customer_phone = data.get('customer_phone', '')
    if customer_phone:
        reshaped_phone_label = get_display(arabic_reshaper.reshape("التليفون: "))
        pdf.cell(0, 8, f"{customer_phone} {reshaped_phone_label}", 0, 1, 'R')
    
    due_date = data.get('due_date')
    if due_date:
        due_date_ar = convert_numbers(due_date)
        reshaped_due_date_label = get_display(arabic_reshaper.reshape("تاريخ التسليم: "))
        pdf.cell(0, 8, f"{due_date_ar} {reshaped_due_date_label}", 0, 1, 'R')

    pdf.cell(0, 5, dashed_line, 0, 1, 'C')
    
    # --- جدول الأصناف ---
    pdf.set_font('Arial', '', 9)
    col_width = (RECEIPT_WIDTH - 2 * MARGIN)
    col_desc = col_width * 0.45
    col_qty = col_width * 0.15
    col_price = col_width * 0.20
    col_total = col_width * 0.20
    
    pdf.cell(col_total, 8, get_display(arabic_reshaper.reshape("الإجمالي")), 0, 0, 'C')
    pdf.cell(col_price, 8, get_display(arabic_reshaper.reshape("السعر")), 0, 0, 'C')
    pdf.cell(col_qty, 8, get_display(arabic_reshaper.reshape("الكمية")), 0, 0, 'C')
    pdf.cell(col_desc, 8, get_display(arabic_reshaper.reshape("الصنف")), 0, 1, 'R')
    pdf.cell(0, 3, "-" * 50, 0, 1, 'C')
    total_from_items = sum(item.get('subtotal', 0) for item in data.get('items', []))

    for item in data.get('items', []):
        desc = get_display(arabic_reshaper.reshape(str(item.get('description', ''))))
        qty = convert_numbers(item.get('quantity', 1))
        price = convert_numbers(f"{item.get('unit_price', 0):.2f}")
        subtotal = convert_numbers(f"{item.get('subtotal', 0):.2f}")
        
        pdf.cell(col_total, 8, subtotal, 0, 0, 'C')
        pdf.cell(col_price, 8, price, 0, 0, 'C')
        pdf.cell(col_qty, 8, qty, 0, 0, 'C')
        pdf.cell(col_desc, 8, desc, 0, 1, 'R')
    
    pdf.ln(2)
    pdf.cell(0, 5, dashed_line, 0, 1, 'C')
    
    # --- الملخص المالي ---
    currency_ar = get_display(arabic_reshaper.reshape("ج.م"))
    summary_items = [
        ("الإجمالي قبل الخصم:", f"{total_from_items:.2f}"),
        ("الخصم:", f"{data.get('discount', 0):.2f}"),
        ("الإجمالي بعد الخصم:", f"{(total_from_items - data.get('discount', 0)):.2f}"),
        ("المدفوع:", f"{data.get('paid', 0):.2f}"),
    ]

    for label, value in summary_items:
        reshaped_label = get_display(arabic_reshaper.reshape(label))
        value_ar = convert_numbers(value)
        pdf.cell(0, 7, f"{value_ar} {currency_ar}{' ' * 5}{reshaped_label}", 0, 1, 'R')
        
    pdf.cell(0, 3, "-" * 50, 0, 1, 'C')
    
    remaining_ar = convert_numbers(f"{data.get('remaining', 0):.2f}")
    reshaped_remaining_label = get_display(arabic_reshaper.reshape("المتبقي: "))
    pdf.cell(0, 8, f"{remaining_ar} {currency_ar}{' ' * 5}{reshaped_remaining_label}", 0, 1, 'R')

    pdf.cell(0, 5, dashed_line, 0, 1, 'C')

    pdf.ln(5)
    thank_you_reshaped = get_display(arabic_reshaper.reshape("شكرا لتعاملكم معنا!"))
    pdf.cell(0, 10, thank_you_reshaped, 0, 0, 'C')

    try:
        pdf.output(filename)
        print(f"تم إنشاء الفاتورة بنجاح باسم: {filename}")
    except Exception as e:
        print(f"فشلت عملية حفظ ملف PDF. الخطأ: {e}")
def generate_preview_image(filename, data):
    IMG_WIDTH = 576
    MARGIN = 20
    LINE_HEIGHT = 40
    FONT_SIZE_NORMAL = 28
    FONT_SIZE_LARGE = 36

    try:
        font_path = "arial.ttf" 
        font_normal = ImageFont.truetype(font_path, FONT_SIZE_NORMAL)
    except IOError:
        messagebox.showerror("خطأ", f"لم يتم العثور على ملف الخط '{font_path}'.")
        return

    full_text = format_receipt_for_display(**data)
    
    img_height = (len(full_text.split('\n')) * LINE_HEIGHT) + 200
    image = Image.new('RGB', (IMG_WIDTH, img_height), color='white')
    draw = ImageDraw.Draw(image)
    y_pos = MARGIN

    if os.path.exists('logo.png'):
        try:
            logo = Image.open('logo.png')
            logo.thumbnail((IMG_WIDTH - 2 * MARGIN, 150))
            image.paste(logo, ((IMG_WIDTH - logo.width) // 2, y_pos))
            y_pos += logo.height + LINE_HEIGHT
        except Exception as e:
            print(f"Error loading logo: {e}")

    for line in full_text.split('\n'):
        reshaped_line = get_display(arabic_reshaper.reshape(line))
        draw.text((IMG_WIDTH - MARGIN, y_pos), reshaped_line, font=font_normal, fill='black', anchor="ra", align="right")
        y_pos += LINE_HEIGHT

    image.save(filename)
    os.startfile(filename)
def print_escpos_receipt(printer, data):
    """
    تأخذ هذه الدالة كائن الطابعة وبيانات الفاتورة وتطبعها بتنسيق مناسب للطابعات الحرارية.
    """
    try:
        # إعدادات الطابعة للغة العربية
        
        # --- FIX ---
        # We are removing this line because the printer does not recognize the command.
        # printer.hw('init') 
        
        printer.charcode('CP864') # جدول ترميز يدعم العربية
        printer.set(align='center')

        # طباعة الشعار إذا كان موجودًا
        if os.path.exists('logo.png'):
            printer.image('logo.png')
            printer.ln()

        # طباعة رأس الفاتورة
        printer.text(reshape_arabic("التوفيق\n"))
        printer.text(reshape_arabic("تليفون: 01080324634\n"))
        printer.text(reshape_arabic("7 خاتم المرسلين- خلف عمارات بنك مصر\n"))
        printer.text("----------------------------------------\n")

        # طباعة معلومات الفاتورة (محاذاة لليمين)
        printer.set(align='right')
        date_str = data['timestamp'].strftime('%Y-%m-%d')
        printer.text(reshape_arabic(f"فاتورة رقم: {data['receipt_id']}\n"))
        printer.text(reshape_arabic(f"التاريخ: {date_str}\n"))
        printer.text(reshape_arabic(f"العميل: {data['customer_name']}\n"))
        if data.get('customer_phone'): # Use .get for safety
            printer.text(reshape_arabic(f"تليفون: {data['customer_phone']}\n"))
        if data['due_date'] and data['due_date'].strip():
            printer.text(reshape_arabic(f"تاريخ التسليم: {data['due_date']}\n"))
        printer.text("----------------------------------------\n")
        
        # طباعة عناوين جدول الأصناف
        # الطابعات الحرارية لا تدعم الجداول المعقدة، سنبسطها
        printer.text(reshape_arabic("الصنف                الكمية   السعر  الإجمالي\n"))
        printer.text("----------------------------------------\n")

        # طباعة الأصناف
        for item in data['items']:
            desc = item.get('description', '')
            qty = str(item.get('quantity', 1))
            price = f"{item.get('unit_price', 0):.2f}"
            subtotal = f"{item.get('subtotal', 0):.2f}"

            # نقوم بعمل reshape لكل جزء نصي عربي
            reshaped_desc = reshape_arabic(desc)
            
            # محاولة تنسيق السطر
            # هذا الجزء قد يحتاج لتعديل حسب عرض الطابعة لديك
            line = f"{reshaped_desc[:18]:<18} {qty:>5} {price:>7} {subtotal:>8}\n"
            printer.text(line)

        printer.text("----------------------------------------\n")
        
        # طباعة ملخص الفاتورة
        total_from_items = sum(item.get('subtotal', 0) for item in data['items'])
        printer.text(reshape_arabic(f"الإجمالي قبل الخصم: {total_from_items:.2f} ج.م\n"))
        printer.text(reshape_arabic(f"الخصم: {data['discount']:.2f} ج.م\n"))
        printer.text(reshape_arabic(f"الإجمالي بعد الخصم: {(total_from_items - data['discount']):.2f} ج.م\n"))
        printer.text(reshape_arabic(f"المدفوع: {data['paid']:.2f} ج.م\n"))
        
        # طباعة المتبقي بخط عريض للتأكيد
        printer.set(align='right', bold=True, double_height=True, double_width=True)
        printer.text(reshape_arabic(f"المتبقي: {data['remaining']:.2f} ج.م\n"))
        printer.set(align='right') # إعادة الخط لوضعه الطبيعي
        printer.text("----------------------------------------\n")

        # طباعة الملاحظات إن وجدت
        if data['notes'] and data['notes'].strip():
            printer.text(reshape_arabic("ملاحظات:\n"))
            printer.text(reshape_arabic(data['notes'] + "\n"))
            printer.text("----------------------------------------\n")

        # طباعة رسالة الشكر
        printer.set(align='center')
        printer.text(reshape_arabic("شكراً لتعاملكم معنا!\n\n\n"))

        # --- تأكد من تفعيل هذا السطر ---
        # هذا الأمر يخبر الطابعة بإنهاء المهمة وقص الورق، مما قد يحل مشكلة قطع الاتصال
        printer.cut()
        
        messagebox.showinfo("نجاح", "تم إرسال الفاتورة إلى الطابعة بنجاح.")

    except Exception as e:
        messagebox.showerror("خطأ طباعة", f"فشلت عملية الطباعة. تأكد من توصيل الطابعة وتشغيلها.\n\nالخطأ: {e}")

# ==============================================================================
# 5. الكلاس الرئيسي للتطبيق (بدون تغيير)
# ==============================================================================
class CashierApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.current_customer_id = None
        self.current_customer_name = None
        self.current_order_items = []
        self.current_user = None
        self.intermediate_item = {}
        self.selected_print_type = None
        self.withdraw()
        self.create_login_window()
        
    # ... (create_login_window and check_login remain the same) ...
    def create_login_window(self):
        self.login_window = ctk.CTkToplevel(self)
        self.login_window.title("تسجيل الدخول")
        self.login_window.geometry("500x500")
        self.login_window.resizable(False, False)
        self.login_window.transient(self)
        self.login_window.grab_set()
        
        try:
            logo_image = ctk.CTkImage(Image.open("logo.png"), size=(120, 120))
            logo_label = ctk.CTkLabel(self.login_window, image=logo_image, text="")
            logo_label.pack(pady=(20, 10))
        except FileNotFoundError:
            print("Warning: 'logo.png' not found.")
        
        ctk.CTkLabel(self.login_window, text="اسم المستخدم:", font=("Arial", 16)).pack(pady=(10, 5))
        self.username_entry = ctk.CTkEntry(self.login_window, width=250, height=35, font=("Arial", 14))
        self.username_entry.pack()
        ctk.CTkLabel(self.login_window, text="كلمة المرور:", font=("Arial", 16)).pack(pady=(10, 5))
        self.password_entry = ctk.CTkEntry(self.login_window, show="*", width=250, height=35, font=("Arial", 14))
        self.password_entry.pack()
        ctk.CTkButton(self.login_window, text="دخـــول", command=self.check_login, font=("Arial", 16, "bold"), height=40).pack(pady=20)
        self.login_window.protocol("WM_DELETE_WINDOW", self.quit)

    def check_login(self):
        username = self.username_entry.get()
        password = self.password_entry.get()
        if USERS.get(username) == password:
            self.current_user = username
            self.login_window.destroy()
            self.deiconify()
            self.setup_main_ui()
        else:
            messagebox.showerror("خطأ", "اسم المستخدم أو كلمة المرور غير صحيحة")


    def setup_main_ui(self):
        self.title("نظام إدارة المطبعة - الإصدار الاحترافي")
        self.geometry("1350x750")
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)
        
        navigation_frame = ctk.CTkFrame(self, width=180, corner_radius=0)
        navigation_frame.grid(row=0, column=0, sticky="nsew")
        navigation_frame.grid_rowconfigure(10, weight=1) # <<<--- زيادة الرقم لاستيعاب الزر الجديد

        try:
            sidebar_logo_image = ctk.CTkImage(Image.open("logo.png"), size=(100, 100))
            sidebar_logo_label = ctk.CTkLabel(navigation_frame, image=sidebar_logo_image, text="")
            sidebar_logo_label.grid(row=0, column=0, padx=20, pady=(20, 10))
        except FileNotFoundError:
            pass
            
        ctk.CTkLabel(navigation_frame, text=" المطبعة ", font=ctk.CTkFont(size=20, weight="bold")).grid(row=1, column=0, padx=20, pady=(10, 20))
        
        self.nav_buttons = {}
        # <<<--- إضافة جديدة: إضافة صفحة المخزون إلى القائمة --- >>>
        nav_items = {
            "AdminDashboard": "🏠  لوحة التحكم",
            "Page_CustomerSelection": "➕  طلب جديد",
            "Page_JobTracking": "📂  متابعة الطلبات",
            "Page_DebtsTracking": "💰  متابعة الديون",
            "Page_CustomerManagement": "👥  إدارة العملاء",
            "Page_Analysis": "📊  التحليل المالي",
            "Page_InventoryManagement": "📦  إدارة المخزون",
            "Page_PriceManagement": "⚙️  إدارة الأسعار"
        }
        for i, (page_name, text) in enumerate(nav_items.items()):
            button = ctk.CTkButton(navigation_frame, text=text, corner_radius=0, height=40,
                                   border_spacing=10, fg_color="transparent",
                                   text_color=("gray10", "gray90"), hover_color=("gray70", "gray30"),
                                   anchor="e", command=lambda p=page_name: self.show_frame(p))
            button.grid(row=i + 2, column=0, sticky="ew")
            self.nav_buttons[page_name] = button
        
        # <<<--- إضافة جديدة: إخفاء صفحة المخزون من غير الأدمن --- >>>
        if self.current_user != 'admin':
            self.nav_buttons["Page_PriceManagement"].grid_forget()
            self.nav_buttons["Page_InventoryManagement"].grid_forget()

        self.main_container = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        self.main_container.grid(row=0, column=1, sticky="nsew")
        self.main_container.grid_rowconfigure(0, weight=1)
        self.main_container.grid_columnconfigure(0, weight=1)
        
        self.frames = {}
        # <<<--- إضافة جديدة: تسجيل صفحة المخزون --- >>>
        for F in (Page_CustomerSelection, Page1_PrintType, Page2_Details, Page_PlainPaper, Page_IDCards, Page_Addons, Page_Preparation, Page_CartAndCheckout, Page3_Receipt, AdminDashboard, Page_Analysis, Page_CustomerManagement, Page_JobTracking, Page_DebtsTracking, Page_PriceManagement, Page_InventoryManagement):
            page_name = F.__name__
            frame = F(parent=self.main_container, controller=self)
            self.frames[page_name] = frame
            frame.grid(row=0, column=0, sticky="nsew")
            
        self.show_frame("AdminDashboard")

    def show_frame(self, page_name, data=None):
        for name, button in self.nav_buttons.items():
            button.configure(fg_color=("gray75", "gray25") if name == page_name else "transparent")
        
        if page_name == 'Page_PriceManagement':
            self.frames[page_name].populate_prices()
        # <<<--- إضافة جديدة: تحديث المخزون عند عرض الصفحة --- >>>
        if page_name == 'Page_InventoryManagement':
            self.frames[page_name].load_inventory()
            
        frame = self.frames[page_name]

        if page_name == 'AdminDashboard': frame.load_daily_summary()
        if page_name == 'Page_CartAndCheckout': frame.refresh_cart_display()
        if page_name == 'Page_Analysis': frame.populate_year_selector()
        if page_name == 'Page_CustomerManagement': frame.load_all_customers()
        if page_name == 'Page_JobTracking': frame.load_open_jobs()
        if page_name == 'Page_DebtsTracking': frame.load_debts()
        if page_name in ['Page_Addons', 'Page_Preparation'] and data:
            frame.update_view(data)

        frame.tkraise()
    
    # ... (باقي كود الكلاس الرئيسي يبقى كما هو) ...
    def get_frame(self, page_name):
        return self.frames[page_name]

    def add_item_to_order(self, item_details):
        self.current_order_items.append(item_details)
        if "Page1_PrintType" in self.frames:
            self.frames["Page1_PrintType"].update_cart_button()

    def clear_current_order(self):
        self.current_order_items = []
        # <<<--- إضافة جديدة: مسح المواد المستهلكة عند مسح الطلب --- >>>
        if "Page_CartAndCheckout" in self.frames:
            self.frames["Page_CartAndCheckout"].consumed_materials.clear()
        if "Page1_PrintType" in self.frames:
            self.frames["Page1_PrintType"].update_cart_button()

    def save_receipt(self, receipt_text, total_amount, customer_id, due_date, notes, discount, amount_paid, remaining):
        conn = sqlite3.connect('receipts.db')
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO receipts (timestamp, receipt_data, total_amount, customer_id, status, due_date, notes, discount, amount_paid, remaining_amount) 
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (datetime.now(), receipt_text, total_amount, customer_id, "تحت التنفيذ", due_date, notes, discount, amount_paid, remaining))
        last_id = cursor.lastrowid
        conn.commit()
        # لا تغلق الاتصال هنا، سنحتاجه لحفظ المواد
        return last_id, conn # <<<--- تعديل: إرجاع كائن الاتصال

# ==============================================================================
# 6. كلاسات صفحات الواجهة
# ==============================================================================

# ... (كل كلاسات الصفحات من Page_CustomerSelection إلى Page_Preparation تبقى كما هي) ...

class Page_CustomerSelection(ctk.CTkFrame):
    def __init__(self, parent, controller):
        super().__init__(parent)
        self.controller = controller
        ctk.CTkLabel(self, text="ابدأ طلب جديد", font=ctk.CTkFont(size=24, weight="bold")).pack(pady=40)
        main_frame = ctk.CTkFrame(self)
        main_frame.pack(pady=20, padx=60, expand=True)
        ctk.CTkLabel(main_frame, text="ابحث عن عميل برقم التليفون:", font=("Arial", 18)).pack(pady=(20, 10))
        self.phone_search_entry = ctk.CTkEntry(main_frame, font=("Arial", 16), width=300, height=40)
        self.phone_search_entry.pack(pady=5)
        self.phone_search_entry.bind("<Return>", self.search_customer_event)
        ctk.CTkButton(main_frame, text="بحث وبدء الطلب", font=("Arial", 16, "bold"), height=40, command=self.search_customer).pack(pady=15)
        ctk.CTkLabel(main_frame, text="--- أو ---", font=("Arial", 14)).pack(pady=10)
        ctk.CTkButton(main_frame, text="إضافة عميل جديد وبدء الطلب", font=("Arial", 16), height=40, 
                      fg_color="#3498db", hover_color="#2980b9", command=self.new_customer_popup).pack(pady=15)
        self.result_label = ctk.CTkLabel(main_frame, text="", font=("Arial", 14, "italic"), text_color="gray")
        self.result_label.pack(pady=20)

    def search_customer_event(self, event):
        self.search_customer()

    def search_customer(self):
        phone = self.phone_search_entry.get()
        if not phone:
            messagebox.showwarning("خطأ", "الرجاء إدخال رقم تليفون للبحث.")
            return
        conn = sqlite3.connect('receipts.db')
        cursor = conn.cursor()
        cursor.execute("SELECT id, name FROM customers WHERE phone = ?", (phone,))
        customer = cursor.fetchone()
        conn.close()
        if customer:
            customer_id, customer_name = customer
            self.controller.current_customer_id = customer_id
            self.controller.current_customer_name = customer_name
            self.start_order()
        else:
            self.result_label.configure(text="العميل غير موجود. يمكنك إضافته كعميل جديد.", text_color="orange")

    def new_customer_popup(self):
        popup = ctk.CTkToplevel(self)
        popup.title("إضافة عميل جديد")
        popup.geometry("400x300")
        popup.transient(self)
        popup.grab_set()
        ctk.CTkLabel(popup, text="اسم العميل:", font=("Arial", 14)).pack(pady=(15, 5))
        name_entry = ctk.CTkEntry(popup, width=300)
        name_entry.pack()
        ctk.CTkLabel(popup, text="رقم التليفون:", font=("Arial", 14)).pack(pady=(10, 5))
        phone_entry = ctk.CTkEntry(popup, width=300)
        phone_entry.pack()
        ctk.CTkLabel(popup, text="ملاحظات (اختياري):", font=("Arial", 14)).pack(pady=(10, 5))
        notes_entry = ctk.CTkEntry(popup, width=300)
        notes_entry.pack()
        def save_and_start():
            name = name_entry.get()
            phone = phone_entry.get()
            notes = notes_entry.get()
            if not name or not phone:
                messagebox.showerror("خطأ", "الاسم ورقم التليفون حقول إجبارية.", parent=popup)
                return
            try:
                conn = sqlite3.connect('receipts.db')
                cursor = conn.cursor()
                cursor.execute("INSERT INTO customers (name, phone, notes) VALUES (?, ?, ?)", (name, phone, notes))
                new_id = cursor.lastrowid
                conn.commit()
                conn.close()
                self.controller.current_customer_id = new_id
                self.controller.current_customer_name = name
                self.result_label.configure(text=f"تم إضافة وبدء الطلب للعميل: {name}", text_color="#2ECC71")
                self.phone_search_entry.delete(0, 'end')
                popup.destroy()
                self.start_order()
            except sqlite3.IntegrityError:
                messagebox.showerror("خطأ", "رقم التليفون هذا مسجل لعميل آخر.", parent=popup)
        ctk.CTkButton(popup, text="حفظ وبدء الطلب", command=save_and_start).pack(pady=20)

    def start_order(self):
        if self.controller.current_customer_id is None:
            messagebox.showerror("خطأ", "حدث خطأ ولم يتم تحديد العميل. الرجاء المحاولة مرة أخرى.")
            return
        self.controller.clear_current_order()
        self.controller.show_frame("Page1_PrintType")
class Page1_PrintType(ctk.CTkFrame):
    def __init__(self, parent, controller):
        super().__init__(parent)
        self.controller = controller
        ctk.CTkLabel(self, text="الخطوة 1: اختر نوع الطباعة", font=ctk.CTkFont(size=24, weight="bold")).pack(pady=20)
        scrollable_frame = ctk.CTkScrollableFrame(self)
        scrollable_frame.pack(fill="both", expand=True, padx=100, pady=20)
        
        ctk.CTkLabel(scrollable_frame, text="--- ورق طبع ---", font=("Arial", 16, "bold")).pack(pady=(10, 5))
        for p_type in PLAIN_PAPER_TYPES:
            btn = ctk.CTkButton(scrollable_frame, text=p_type, font=("Arial", 16), height=40, fg_color="#3498db", hover_color="#2980b9",
                              command=lambda pt=p_type: self.select_and_next(pt))
            btn.pack(fill="x", pady=5, padx=10)

        ctk.CTkLabel(scrollable_frame, text="--- منتجات أخرى ---", font=("Arial", 16, "bold")).pack(pady=(20, 5))
        
        btn_id = ctk.CTkButton(scrollable_frame, text="كروت ID", font=("Arial", 16), height=40,
                               fg_color="#9b59b6", hover_color="#8e44ad",
                               command=lambda: self.select_and_next("كروت ID"))
        btn_id.pack(fill="x", pady=5, padx=10)

        ctk.CTkLabel(scrollable_frame, text="--- كوشيه واستيكر ---", font=("Arial", 16, "bold")).pack(pady=(20, 5))
        for p_type in PRINTING_PRICES.keys():
            btn = ctk.CTkButton(scrollable_frame, text=p_type, font=("Arial", 16), height=40,
                              command=lambda pt=p_type: self.select_and_next(pt))
            btn.pack(fill="x", pady=5, padx=10)
        
        bottom_frame = ctk.CTkFrame(self, fg_color="transparent")
        bottom_frame.pack(pady=20, fill="x", padx=100)
        admin_btn = ctk.CTkButton(bottom_frame, text="إلغاء الطلب والعودة", fg_color="#c0392b", hover_color="#e74c3c", command=self.cancel_order)
        admin_btn.pack(side="right", padx=10)
        self.cart_button = ctk.CTkButton(bottom_frame, text="عرض الطلب الحالي (0)", fg_color="#27ae60", hover_color="#2ecc71",
                                         font=("Arial", 14, "bold"), state="disabled",
                                         command=lambda: controller.show_frame("Page_CartAndCheckout"))
        self.cart_button.pack(side="left", padx=10)
        
    def update_cart_button(self):
        item_count = len(self.controller.current_order_items)
        self.cart_button.configure(text=f"عرض الطلب الحالي ({item_count})")
        self.cart_button.configure(state="normal" if item_count > 0 else "disabled")
        
    def select_and_next(self, print_type):
        self.controller.selected_print_type = print_type
        if print_type in PLAIN_PAPER_TYPES:
            self.controller.show_frame("Page_PlainPaper")
        elif print_type == "كروت ID":
            self.controller.show_frame("Page_IDCards")
        else:
            self.controller.show_frame("Page2_Details")
            
    def cancel_order(self):
        if self.controller.current_order_items:
            if messagebox.askyesno("تأكيد", "يوجد طلب مفتوح. هل تريد إلغاءه والعودة؟"):
                self.controller.clear_current_order()
                self.controller.current_customer_id = None
                self.controller.current_customer_name = None
                self.controller.show_frame("Page_CustomerSelection")
        else:
            self.controller.show_frame("Page_CustomerSelection")
class Page_PlainPaper(ctk.CTkFrame):
    def __init__(self, parent, controller):
        super().__init__(parent)
        self.controller = controller
        self.print_method_var = ctk.StringVar(value="Ink")
        self.size_var = ctk.StringVar(value="A4")
        self.side_var = ctk.StringVar(value="وجه")
        self.is_book_mode = ctk.BooleanVar(value=False)
        ctk.CTkLabel(self, text="تفاصيل طباعة الورق العادي", font=ctk.CTkFont(size=24, weight="bold")).pack(pady=20)
        main_frame = ctk.CTkFrame(self)
        main_frame.pack(pady=20, padx=50, fill="y", expand=True)
        ctk.CTkLabel(main_frame, text="نوع الطباعة:", font=("Arial", 16, "bold")).pack(pady=(10, 5))
        method_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        method_frame.pack(pady=5)
        ctk.CTkRadioButton(method_frame, text="ليزر", variable=self.print_method_var, value="ليزر").pack(side="right", padx=10)
        ctk.CTkRadioButton(method_frame, text="Ink", variable=self.print_method_var, value="Ink").pack(side="right", padx=10)
        ctk.CTkRadioButton(method_frame, text="أبيض وأسود", variable=self.print_method_var, value="أبيض وأسود").pack(side="right", padx=10)
        ctk.CTkLabel(main_frame, text="حجم الورق:", font=("Arial", 16)).pack(pady=10)
        ctk.CTkRadioButton(main_frame, text="A4", variable=self.size_var, value="A4").pack()
        ctk.CTkRadioButton(main_frame, text="A3", variable=self.size_var, value="A3").pack()
        ctk.CTkCheckBox(main_frame, text="هل هذا طلب لكتب/كتيبات؟", variable=self.is_book_mode, font=("Arial", 14, "bold"), command=self.toggle_book_mode).pack(pady=(20, 10))
        self.loose_paper_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        ctk.CTkLabel(self.loose_paper_frame, text="العدد الإجمالي للورق:", font=("Arial", 16)).pack(pady=5)
        self.total_papers_entry = ctk.CTkEntry(self.loose_paper_frame, font=("Arial", 14))
        self.total_papers_entry.pack(pady=5)
        self.book_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        ctk.CTkLabel(self.book_frame, text="عدد ورق الكتاب الواحد:", font=("Arial", 16)).pack(pady=5)
        self.papers_per_book_entry = ctk.CTkEntry(self.book_frame, font=("Arial", 14))
        self.papers_per_book_entry.pack(pady=5)
        ctk.CTkLabel(self.book_frame, text="عدد الكتب المطلوبة:", font=("Arial", 16)).pack(pady=5)
        self.book_count_entry = ctk.CTkEntry(self.book_frame, font=("Arial", 14))
        self.book_count_entry.pack(pady=5)
        ctk.CTkLabel(main_frame, text="أوجه الطباعة:", font=("Arial", 16)).pack(pady=10)
        ctk.CTkRadioButton(main_frame, text="وجه واحد", variable=self.side_var, value="وجه").pack()
        ctk.CTkRadioButton(main_frame, text="وجهين", variable=self.side_var, value="وجهين").pack()
        button_frame = ctk.CTkFrame(self, fg_color="transparent")
        button_frame.pack(pady=20)
        ctk.CTkButton(button_frame, text="رجوع", command=lambda: controller.show_frame("Page1_PrintType")).pack(side="right", padx=10)
        ctk.CTkButton(button_frame, text="التالي (الإضافات)", font=("Arial", 16, "bold"), height=40, command=self.calculate_and_proceed).pack(side="left", padx=10)
        self.toggle_book_mode()

    def toggle_book_mode(self):
        if self.is_book_mode.get():
            self.loose_paper_frame.pack_forget()
            self.book_frame.pack(pady=10, fill="x")
        else:
            self.book_frame.pack_forget()
            self.loose_paper_frame.pack(pady=10, fill="x")

    def calculate_and_proceed(self):
        total_papers = 0
        book_count = 1
        papers_per_book = 0  # تعريف المتغير مبكراً
        is_book_order = self.is_book_mode.get()
        
        try:
            if is_book_order:
                papers_per_book = int(self.papers_per_book_entry.get())
                book_count = int(self.book_count_entry.get())
                if papers_per_book <= 0 or book_count <= 0:
                    raise ValueError
                total_papers = papers_per_book * book_count
            else:
                total_papers = int(self.total_papers_entry.get())
                if total_papers <= 0:
                    raise ValueError
        except (ValueError, TypeError):
            messagebox.showerror("خطأ", "الرجاء إدخال أرقام صحيحة وموجبة.")
            return
        
        p_type = self.controller.selected_print_type
        size = self.size_var.get()
        side = self.side_var.get()
        print_method = self.print_method_var.get()
        
        price_per_sheet = 0
        if print_method == "ليزر":
            price_per_sheet = LASER_PLAIN_PAPER_PRICES[p_type][size][side]
        else:
            quantity_bracket = 'large' if total_papers > QUANTITY_THRESHOLD else 'small'
            price_per_sheet = PLAIN_PAPER_PRICES[p_type][size][quantity_bracket][side]
            
        printing_total = price_per_sheet * total_papers
        
        # هنا تم تجميع البيانات لإرسالها للخطوة التالية
        self.controller.intermediate_item = {
            "type": "plain_paper",
            "paper_size": size,
            "is_book_order": is_book_order,
            "papers_per_book": papers_per_book,  # تم تمرير عدد الورق هنا
            "description": f"{p_type} ({size})",
            "printing_cost": printing_total,
            "items_to_finish": book_count if is_book_order else total_papers
        }
        self.controller.show_frame("Page_Addons", data=self.controller.intermediate_item)
class Page2_Details(ctk.CTkFrame):
    def __init__(self, parent, controller):
        super().__init__(parent)
        self.controller = controller
        
        # --- المتغيرات ---
        self.printing_side_var = ctk.StringVar(value="وجه")
        self.calculation_method_var = ctk.StringVar(value="نسخ")
        self.lakta_price_var = ctk.DoubleVar(value=LAKTA_PRICES[0])

        # --- الواجهة الرئيسية ---
        ctk.CTkLabel(self, text="تفاصيل طباعة الكوشيه والاستيكر", font=ctk.CTkFont(size=24, weight="bold")).pack(pady=20)
        
        main_frame = ctk.CTkFrame(self)
        main_frame.pack(pady=20, padx=50, fill="y", expand=True)

        # --- اختيار طريقة الحساب ---
        ctk.CTkLabel(main_frame, text="اختر طريقة الحساب:", font=("Arial", 16, "bold")).pack(pady=(10, 5))
        method_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        method_frame.pack(pady=5)
        ctk.CTkRadioButton(method_frame, text="حساب بالنسخ", variable=self.calculation_method_var, value="نسخ", command=self.toggle_view).pack(side="right", padx=10)
        ctk.CTkRadioButton(method_frame, text="حساب باللقطات", variable=self.calculation_method_var, value="لقطات", command=self.toggle_view).pack(side="right", padx=10)

        # --- إطار إدخال النسخ (الطريقة القديمة) ---
        self.copies_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        ctk.CTkLabel(self.copies_frame, text="عدد النسخ النهائية:", font=("Arial", 16)).pack(pady=5)
        self.copies_entry = ctk.CTkEntry(self.copies_frame, font=("Arial", 14))
        self.copies_entry.pack(pady=5)

        # --- إطار إدخال اللقطات (الطريقة الجديدة) ---
        self.lakta_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        ctk.CTkLabel(self.lakta_frame, text="عدد اللقطات:", font=("Arial", 16)).pack(pady=5)
        self.lakta_entry = ctk.CTkEntry(self.lakta_frame, font=("Arial", 14))
        self.lakta_entry.pack(pady=5)
        
        ctk.CTkLabel(self.lakta_frame, text="سعر اللقطة (وجه واحد):", font=("Arial", 16)).pack(pady=(10, 5))
        lakta_price_frame = ctk.CTkFrame(self.lakta_frame, fg_color="transparent")
        lakta_price_frame.pack(pady=5)
        # إنشاء أزرار الراديو ديناميكياً من الإعدادات
        for price in LAKTA_PRICES:
            ctk.CTkRadioButton(lakta_price_frame, text=f"{price} ج.م", variable=self.lakta_price_var, value=price).pack(side="right", padx=10)

        # --- خيارات مشتركة (وجه/وجهين) ---
        ctk.CTkLabel(main_frame, text="أوجه الطباعة:", font=("Arial", 16)).pack(pady=10)
        sides_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        sides_frame.pack()
        ctk.CTkRadioButton(sides_frame, text="وجه واحد", variable=self.printing_side_var, value="وجه").pack(side="right", padx=10)
        ctk.CTkRadioButton(sides_frame, text="وجهين", variable=self.printing_side_var, value="وجهين").pack(side="right", padx=10)

        # --- أزرار التحكم السفلية ---
        button_frame = ctk.CTkFrame(self, fg_color="transparent")
        button_frame.pack(pady=20)
        ctk.CTkButton(button_frame, text="رجوع", command=lambda: controller.show_frame("Page1_PrintType")).pack(side="right", padx=10)
        ctk.CTkButton(button_frame, text="التالي (الإضافات)", font=("Arial", 16, "bold"), height=40, command=self.calculate_and_proceed).pack(side="left", padx=10)

        # عرض الإطار الافتراضي عند بدء التشغيل
        self.toggle_view()

    def toggle_view(self):
        """تظهر أو تخفي حقول الإدخال بناءً على طريقة الحساب المختارة."""
        if self.calculation_method_var.get() == "نسخ":
            self.lakta_frame.pack_forget()
            self.copies_frame.pack(pady=10, fill="x")
        else: # "لقطات"
            self.copies_frame.pack_forget()
            self.lakta_frame.pack(pady=10, fill="x")

    def calculate_and_proceed(self):
        p_type = self.controller.selected_print_type
        side = self.printing_side_var.get()
        calculation_method = self.calculation_method_var.get()

        printing_total = 0
        items_to_finish = 0
        description = p_type

        if 'استيكر' in p_type and side == 'وجهين':
            messagebox.showwarning("تنبيه", "الاستيكر له وجه طباعة واحد فقط. سيتم الحساب على أنه وجه واحد.")
            side = 'وجه'
        
        side_multiplier = 2 if side == 'وجهين' else 1

        try:
            if calculation_method == "نسخ":
                copies = int(self.copies_entry.get())
                if copies <= 0: raise ValueError("العدد يجب أن يكون أكبر من صفر")
                
                base_price_per_copy = PRINTING_PRICES[p_type].get(side, PRINTING_PRICES[p_type]['وجه'])
                printing_total = base_price_per_copy * copies
                items_to_finish = copies

            elif calculation_method == "لقطات":
                num_laktat = int(self.lakta_entry.get())
                if num_laktat <= 0: raise ValueError("العدد يجب أن يكون أكبر من صفر")
                
                price_per_lakta = self.lakta_price_var.get()
                printing_total = num_laktat * price_per_lakta * side_multiplier
                items_to_finish = num_laktat
                description = f"{p_type} (لقطات)" # تغيير الوصف لتمييزه

        except (ValueError, TypeError) as e:
            messagebox.showerror("خطأ في الإدخال", f"الرجاء إدخال عدد صحيح وموجب. \n{e}")
            return
        
        self.controller.intermediate_item = {
            "type": "kocheh", 
            "paper_size": "A3+", 
            "is_book_order": True, # نعاملها ككتب لأن كل لقطة قد تحتاج تشطيب منفصل
            "description": description,
            "printing_cost": printing_total,
            "items_to_finish": items_to_finish
        }
        self.controller.show_frame("Page_Addons", data=self.controller.intermediate_item)
class Page_IDCards(ctk.CTkFrame):
    def __init__(self, parent, controller):
        super().__init__(parent)
        self.controller = controller
        
        ctk.CTkLabel(self, text="تفاصيل طلب كروت ID", font=ctk.CTkFont(size=24, weight="bold")).pack(pady=30)
        
        main_frame = ctk.CTkFrame(self)
        main_frame.pack(pady=20, padx=50, expand=True)
        
        ctk.CTkLabel(main_frame, text="أدخل عدد الكروت المطلوبة:", font=("Arial", 18)).pack(pady=(20, 10))
        self.quantity_entry = ctk.CTkEntry(main_frame, font=("Arial", 16), width=250, height=40)
        self.quantity_entry.pack(pady=5)
        self.quantity_entry.bind("<KeyRelease>", self.update_price_display)
        
        self.price_label = ctk.CTkLabel(main_frame, text="سعر الكارت الواحد: -- ج.م", font=("Arial", 16, "bold"), text_color="#2ECC71")
        self.price_label.pack(pady=20)
        
        button_frame = ctk.CTkFrame(self, fg_color="transparent")
        button_frame.pack(pady=40)
        ctk.CTkButton(button_frame, text="رجوع", command=lambda: controller.show_frame("Page1_PrintType")).pack(side="right", padx=10)
        ctk.CTkButton(button_frame, text="أضف إلى الطلب", font=("Arial", 16, "bold"), height=40, command=self.add_to_order).pack(side="left", padx=10)

    def get_id_card_price(self, quantity):
        if quantity <= 0: return 0
        for max_qty, price in ID_CARD_PRICING:
            if quantity <= max_qty:
                return price
        return 0

    def update_price_display(self, event=None):
        try:
            quantity = int(self.quantity_entry.get())
            price_per_card = self.get_id_card_price(quantity)
            self.price_label.configure(text=f"سعر الكارت الواحد: {price_per_card:.2f} ج.م")
        except (ValueError, TypeError):
            self.price_label.configure(text="سعر الكارت الواحد: -- ج.م")

    def add_to_order(self):
        try:
            quantity = int(self.quantity_entry.get())
            if quantity <= 0: raise ValueError
        except (ValueError, TypeError):
            messagebox.showerror("خطأ", "الرجاء إدخال كمية صحيحة وموجبة.")
            return

        price_per_card = self.get_id_card_price(quantity)
        total_price = quantity * price_per_card
        
        self.controller.add_item_to_order({
            "description": "كروت ID", "quantity": quantity,
            "unit_price": price_per_card, "subtotal": total_price
        })
        messagebox.showinfo("تم بنجاح", "تمت إضافة الكروت إلى الطلب الحالي.")
        self.quantity_entry.delete(0, 'end')
        self.update_price_display()
        self.controller.show_frame("Page1_PrintType")
class Page_Addons(ctk.CTkFrame):
    def __init__(self, parent, controller):
        super().__init__(parent)
        self.controller = controller
        self.item_data = {}
        self.lamination_var = ctk.StringVar(value=list(LAMINATION_PRICES.keys())[0])
        self.trimming_var = ctk.StringVar(value=list(TRIMMING_PRICES.keys())[0])
        
        ctk.CTkLabel(self, text="الخطوة 2: اختر الإضافات (اختياري)", font=ctk.CTkFont(size=24, weight="bold")).pack(pady=20)
        self.info_label = ctk.CTkLabel(self, text="", font=("Arial", 16, "italic"))
        self.info_label.pack(pady=10)
        
        main_frame = ctk.CTkFrame(self)
        main_frame.pack(pady=20, padx=50, expand=True)
        
        ctk.CTkLabel(main_frame, text="السلوفان:", font=("Arial", 16)).pack()
        ctk.CTkOptionMenu(main_frame, variable=self.lamination_var, values=list(LAMINATION_PRICES.keys())).pack(pady=5)
        
        ctk.CTkLabel(main_frame, text="التشريح:", font=("Arial", 16)).pack()
        ctk.CTkOptionMenu(main_frame, variable=self.trimming_var, values=list(TRIMMING_PRICES.keys())).pack(pady=5)
        
        button_frame = ctk.CTkFrame(self, fg_color="transparent")
        button_frame.pack(pady=20)
        ctk.CTkButton(button_frame, text="رجوع (تعديل الطباعة)", command=self.go_back).pack(side="right", padx=10)
        ctk.CTkButton(button_frame, text="التالي (التجهيز)", font=("Arial", 16, "bold"), height=40,
                      command=self.calculate_and_proceed).pack(side="left", padx=10)

    def update_view(self, data):
        self.item_data = data
        self.info_label.configure(text=f"إضافات لـ '{data['description']}'")
        self.lamination_var.set(list(LAMINATION_PRICES.keys())[0])
        self.trimming_var.set(list(TRIMMING_PRICES.keys())[0])

    def go_back(self):
        if self.item_data.get("type") == "plain_paper":
            self.controller.show_frame("Page_PlainPaper")
        else:
            self.controller.show_frame("Page2_Details")

    def calculate_and_proceed(self):
        self.item_data['lamination_choice'] = self.lamination_var.get()
        self.item_data['trimming_choice'] = self.trimming_var.get()
        self.controller.show_frame("Page_Preparation", data=self.item_data)
class Page_Preparation(ctk.CTkFrame):
    def __init__(self, parent, controller):
        super().__init__(parent)
        self.controller = controller
        self.item_data = {}
        self.binding_var = ctk.StringVar(value='لا يوجد')
        self.stapling_var = ctk.BooleanVar(value=False)
        self.stapling_size_var = ctk.StringVar(value="A4")
        self.menu_lamination_var = ctk.BooleanVar(value=False)
        self.menu_lamination_size_var = ctk.StringVar(value="A4")
        
        ctk.CTkLabel(self, text="الخطوة 3: التجهيز النهائي", font=ctk.CTkFont(size=24, weight="bold")).pack(pady=20)
        self.info_label = ctk.CTkLabel(self, text="", font=("Arial", 16, "italic"))
        self.info_label.pack(pady=10)
        
        main_frame = ctk.CTkScrollableFrame(self)
        main_frame.pack(pady=10, padx=20, fill="both", expand=True)
        
        binding_main_frame = ctk.CTkFrame(main_frame)
        binding_main_frame.pack(fill="x", expand=True, padx=10, pady=10)
        ctk.CTkLabel(binding_main_frame, text="اختر نوع التجليد", font=("Arial", 18, "bold")).pack(pady=5)
        
        manual_frame = ctk.CTkFrame(binding_main_frame)
        manual_frame.pack(fill="x", pady=5, padx=5)
        ctk.CTkLabel(manual_frame, text="تجليد (بشر وشرشره):", font=("Arial", 14, "bold")).pack(side="right", padx=10)
        for key in [' 5', ' 7', ' 10']:
            ctk.CTkRadioButton(manual_frame, text=key.strip(), variable=self.binding_var, value=key).pack(side="right", padx=10)
        
        staple_frame = ctk.CTkFrame(binding_main_frame)
        staple_frame.pack(fill="x", pady=5, padx=5)
        ctk.CTkLabel(staple_frame, text="تجليد دبوس:", font=("Arial", 14, "bold")).pack(side="right", padx=10)
        for key in [' 3 ', ' 5 ', ' 7 ']:
            ctk.CTkRadioButton(staple_frame, text=key, variable=self.binding_var, value=key).pack(side="right", padx=10)

        self.hardcover_frame = ctk.CTkFrame(binding_main_frame)
        self.hardcover_frame.pack(fill="x", pady=5, padx=5)
        ctk.CTkLabel(self.hardcover_frame, text="هارد كافر:", font=("Arial", 14, "bold")).pack(side="right", padx=10)
        ctk.CTkRadioButton(self.hardcover_frame, text='A5', variable=self.binding_var, value='هارد كافر A5').pack(side="right", padx=10)
        ctk.CTkRadioButton(self.hardcover_frame, text='A4', variable=self.binding_var, value='هارد كافر A4').pack(side="right", padx=10)
        ctk.CTkRadioButton(self.hardcover_frame, text='A3', variable=self.binding_var, value='هارد كافر A3').pack(side="right", padx=10)

        wire_frame = ctk.CTkFrame(binding_main_frame)
        wire_frame.pack(fill="x", pady=5, padx=5)
        ctk.CTkLabel(wire_frame, text="تجليد سلك:", font=("Arial", 14, "bold")).pack(side="right", padx=10)
        for key in ['3_', '5_', '7_', '10_']:
            ctk.CTkRadioButton(wire_frame, text=key, variable=self.binding_var, value=key).pack(side="right", padx=10)
        ctk.CTkRadioButton(binding_main_frame, text="لا يوجد تجليد", variable=self.binding_var, value='لا يوجد').pack(pady=10)

        other_ops_frame = ctk.CTkFrame(main_frame)
        other_ops_frame.pack(fill="x", expand=True, padx=10, pady=10)
        
        ctk.CTkLabel(other_ops_frame, text="سعر القص (إن وجد):", font=("Arial", 16)).pack(pady=(10,0))
        self.cutting_entry = ctk.CTkEntry(other_ops_frame, font=("Arial", 14), placeholder_text="0.0")
        self.cutting_entry.pack(pady=5)
        
        self.stapling_frame = ctk.CTkFrame(other_ops_frame, fg_color="transparent")
        self.stapling_frame.pack(pady=15)
        
        self.stapling_checkbox = ctk.CTkCheckBox(self.stapling_frame, text="هل يوجد بشر؟", variable=self.stapling_var, font=("Arial", 14))
        self.stapling_checkbox.pack(side="right", padx=10)
        ctk.CTkRadioButton(self.stapling_frame, text="A4", variable=self.stapling_size_var, value="A4").pack(side="right", padx=5)
        ctk.CTkRadioButton(self.stapling_frame, text="A5", variable=self.stapling_size_var, value="A5").pack(side="right", padx=5)

        self.menu_lamination_frame = ctk.CTkFrame(other_ops_frame, border_width=1, border_color="gray50")
        ctk.CTkCheckBox(self.menu_lamination_frame, text="تغليف منيو حراري", variable=self.menu_lamination_var, font=("Arial", 16, "bold")).pack(pady=(10, 5))
        
        menu_details_frame = ctk.CTkFrame(self.menu_lamination_frame, fg_color="transparent")
        menu_details_frame.pack(pady=5, padx=10)
        
        ctk.CTkLabel(menu_details_frame, text="عدد المنيوهات:").pack(side="right")
        self.menu_quantity_entry = ctk.CTkEntry(menu_details_frame, width=80)
        self.menu_quantity_entry.pack(side="right", padx=5)
        
        ctk.CTkRadioButton(menu_details_frame, text="A5", variable=self.menu_lamination_size_var, value="A5").pack(side="right", padx=5)
        ctk.CTkRadioButton(menu_details_frame, text="A4", variable=self.menu_lamination_size_var, value="A4").pack(side="right", padx=5)
        ctk.CTkRadioButton(menu_details_frame, text="A3", variable=self.menu_lamination_size_var, value="A3").pack(side="right", padx=5)

        button_frame = ctk.CTkFrame(self, fg_color="transparent")
        button_frame.pack(pady=20)
        ctk.CTkButton(button_frame, text="رجوع (للإضافات)", command=lambda: controller.show_frame("Page_Addons", data=self.item_data)).pack(side="right", padx=10)
        ctk.CTkButton(button_frame, text="أضف إلى الطلب", font=("Arial", 16, "bold"), height=40,
                      command=self.calculate_and_add_to_order).pack(side="left", padx=10)

    def update_view(self, data):
        self.item_data = data
        self.info_label.configure(text=f"تجهيز لـ '{data['description']}' | عدد القطع: {data['items_to_finish']}")
        is_book_order = data.get("is_book_order", False)
        is_sticker_order = "استيكر" in data.get("description", "")
        self.stapling_checkbox.configure(state="normal" if is_book_order and not is_sticker_order else "disabled")
        self.stapling_var.set(False)
        self.binding_var.set('لا يوجد')
        self.cutting_entry.delete(0, 'end')
        self.cutting_entry.insert(0, str(MIN_CUTTING_PRICE))

        self.menu_lamination_var.set(False)
        self.menu_quantity_entry.delete(0, 'end')
        if "كوشيه" in data.get("description", ""):
            self.menu_lamination_frame.pack(fill="x", pady=15, padx=10)
        else:
            self.menu_lamination_frame.pack_forget()

    def get_stapling_price_per_book(self, quantity, size):
        pricing_table = STAPLING_PRICING_A5 if size == 'A5' else STAPLING_PRICING_A4
        for max_qty, price in pricing_table:
            if quantity <= max_qty: return price
        return 0
    
    def get_menu_lamination_price(self, quantity, size):
        if not isinstance(quantity, (int, float)) or quantity <= 0:
            return 0
        for max_qty, prices in MENU_LAMINATION_PRICING:
            if quantity <= max_qty:
                return prices.get(size, 0)
        return 0

    def calculate_and_add_to_order(self):
        items_to_finish = self.item_data.get("items_to_finish", 1)
        if items_to_finish <= 0: items_to_finish = 1
        is_book_order = self.item_data.get("is_book_order", False)
        
        printing_cost = self.item_data.get('printing_cost', 0)
        base_desc_clean = clean_description(self.item_data.get('description', ''))
        self.controller.add_item_to_order({
            "description": base_desc_clean, "quantity": items_to_finish,
            "unit_price": printing_cost / items_to_finish if items_to_finish > 0 else 0,
            "subtotal": printing_cost,
        })

        for choice_key, prices_dict in [('lamination_choice', LAMINATION_PRICES), ('trimming_choice', TRIMMING_PRICES)]:
            choice = self.item_data.get(choice_key)
            if choice and choice != 'لا يوجد':
                price = prices_dict[choice]
                qty = items_to_finish if is_book_order else 1
                self.controller.add_item_to_order({
                    "description": choice, "quantity": qty,
                    "unit_price": price, "subtotal": price * qty
                })
        
        try:
            cutting_price = float(self.cutting_entry.get() or 0)
            if cutting_price > 0:
                self.controller.add_item_to_order({
                    "description": "خدمة قص", "quantity": 1, 
                    "unit_price": cutting_price, "subtotal": cutting_price
                })
        except ValueError:
            messagebox.showerror("خطأ", "الرجاء إدخال سعر قص صحيح."); return

        selected_binding = self.binding_var.get()
        if selected_binding != 'لا يوجد':
            price = BINDING_OPTIONS.get(selected_binding, 0)
            qty = items_to_finish if is_book_order else 1
            desc = clean_description(f"تجليد: {selected_binding.strip()}")
            self.controller.add_item_to_order({
                "description": desc, "quantity": qty,
                "unit_price": price, "subtotal": price * qty
            })
        if self.stapling_var.get():
            # نحصل على عدد الورق للكتاب الواحد من البيانات التي مررناها
            papers_per_book = self.item_data.get("papers_per_book", 0)

            # نتأكد أن هذا طلب كتب بالفعل وبه ورق ليتم تدبيسه
            if is_book_order and papers_per_book > 0:
                size = self.stapling_size_var.get()
                
                # نحسب السعر بناءً على عدد الورق في الكتاب، وليس عدد الكتب
                price_per = self.get_stapling_price_per_book(papers_per_book, size)
                
                desc = clean_description(f"خدمة بشر ({size})")
                
                # نضيف البند للفاتورة (الكمية هنا هي عدد الكتب وهو صحيح)
                self.controller.add_item_to_order({
                    "description": desc,
                    "quantity": items_to_finish, # items_to_finish هو عدد الكتب
                    "unit_price": price_per,      # price_per هو سعر تدبيس الكتاب الواحد
                    "subtotal": price_per * items_to_finish
                })

        if self.menu_lamination_var.get():
            try:
                menu_quantity = int(self.menu_quantity_entry.get())
                if menu_quantity <= 0: raise ValueError
            except (ValueError, TypeError):
                messagebox.showerror("خطأ", "الرجاء إدخال عدد صحيح للمنيوهات.")
                return
            
            menu_size = self.menu_lamination_size_var.get()
            price_per_menu = self.get_menu_lamination_price(menu_quantity, menu_size)
            
            if price_per_menu > 0:
                self.controller.add_item_to_order({
                    "description": f"تغليف منيو حراري ({menu_size})",
                    "quantity": menu_quantity,
                    "unit_price": price_per_menu,
                    "subtotal": price_per_menu * menu_quantity
                })

        messagebox.showinfo("تم بنجاح", "تمت إضافة البنود إلى الطلب.")
        self.controller.show_frame("Page1_PrintType")

# <<<--- تم التعديل على هذا الكلاس بالكامل (Page_CartAndCheckout) --- >>>
class Page_CartAndCheckout(ctk.CTkFrame):
    def __init__(self, parent, controller):
        super().__init__(parent)
        self.controller = controller
        self.consumed_materials = []

        ctk.CTkLabel(self, text="إنهاء الطلب", font=ctk.CTkFont(size=24, weight="bold")).pack(pady=10)
        
        main_container = ctk.CTkFrame(self)
        main_container.pack(fill="both", expand=True, padx=20, pady=5)
        main_container.grid_columnconfigure(0, weight=2)
        main_container.grid_columnconfigure(1, weight=1)
        main_container.grid_rowconfigure(0, weight=1)

        cart_frame = ctk.CTkFrame(main_container)
        cart_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        cart_frame.grid_rowconfigure(0, weight=1)
        cart_frame.grid_columnconfigure(0, weight=1)
        self.cart_textbox = ctk.CTkTextbox(cart_frame, font=("Courier New", 14), state="disabled")
        self.cart_textbox.grid(row=0, column=0, sticky="nsew")

        inventory_frame = ctk.CTkFrame(main_container)
        inventory_frame.grid(row=0, column=1, sticky="nsew")
        inventory_frame.grid_rowconfigure(1, weight=1)
        inventory_frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(inventory_frame, text="المواد المستخدمة (صرف)", font=("Arial", 16, "bold")).grid(row=0, column=0, pady=5)
        self.materials_textbox = ctk.CTkTextbox(inventory_frame, font=("Arial", 12), state="disabled", height=150)
        self.materials_textbox.grid(row=1, column=0, sticky="nsew", padx=5, pady=5)
        ctk.CTkButton(inventory_frame, text="إضافة مادة مستخدمة", command=self.add_consumed_material_popup).grid(row=2, column=0, pady=10, padx=5)

        info_frame = ctk.CTkFrame(self)
        info_frame.pack(fill="x", padx=20, pady=5)
        ctk.CTkLabel(info_frame, text="تاريخ التسليم:", font=("Arial", 14)).pack(side="right", padx=(10,0))
        self.due_date_entry = ctk.CTkEntry(info_frame, placeholder_text=date.today().strftime('%Y-%m-%d'), width=120)
        self.due_date_entry.pack(side="right", padx=(0,10))
        ctk.CTkLabel(info_frame, text="ملاحظات الإنتاج:", font=("Arial", 14)).pack(side="right", padx=10)
        self.notes_entry = ctk.CTkEntry(info_frame)
        self.notes_entry.pack(side="right", padx=10, fill="x", expand=True)
        
        payment_frame = ctk.CTkFrame(self)
        payment_frame.pack(fill="x", padx=20, pady=5)
        ctk.CTkLabel(payment_frame, text="قيمة الخصم:", font=("Arial", 14)).pack(side="right", padx=(10,0))
        self.discount_entry = ctk.CTkEntry(payment_frame, placeholder_text="0.0", width=100)
        self.discount_entry.pack(side="right", padx=(0,20))
        ctk.CTkLabel(payment_frame, text="المبلغ المدفوع:", font=("Arial", 14)).pack(side="right", padx=(10,0))
        self.paid_entry = ctk.CTkEntry(payment_frame, placeholder_text="0.0", width=100)
        self.paid_entry.pack(side="right", padx=(0,20))

        self.total_price_label = ctk.CTkLabel(self, text="", font=("Arial", 26, "bold"), text_color="#2ECC71")
        self.total_price_label.pack(pady=5)
        
        button_frame = ctk.CTkFrame(self, fg_color="transparent")
        button_frame.pack(pady=10, fill="x", padx=50)
        ctk.CTkButton(button_frame, text="إلغاء الطلب", fg_color="#e74c3c", hover_color="#c0392b", command=self.cancel_order).pack(side="right", expand=True, padx=5)
        ctk.CTkButton(button_frame, text="إضافة المزيد", command=lambda: controller.show_frame("Page1_PrintType")).pack(side="right", expand=True, padx=5)
        ctk.CTkButton(button_frame, text="إنهاء وحفظ الفاتورة", font=("Arial", 16, "bold"), height=40, command=self.finalize_order).pack(side="left", expand=True, padx=5)

    def refresh_cart_display(self):
        if self.controller.current_user == 'admin':
            self.discount_entry.configure(state="normal")
        else:
            self.discount_entry.delete(0, 'end')
            self.discount_entry.insert(0, "0.0")
            self.discount_entry.configure(state="disabled")

        self.consumed_materials.clear()
        self.refresh_materials_display()

        self.cart_textbox.configure(state="normal")
        self.cart_textbox.delete("1.0", "end")
        items = self.controller.current_order_items
        if not items:
            self.cart_textbox.insert("1.0", "سلة المشتريات فارغة.")
            self.total_price_label.configure(text="")
            self.cart_textbox.configure(state="disabled")
            return

        grand_total = sum(item['subtotal'] for item in items)
        cart_text = []
        header = f"{'الصنف':<25} {'الكمية':>7} {'الإجمالي':>12}\n"
        separator = "-" * 46 + "\n"
        cart_text.append(header)
        cart_text.append(separator)

        for item in items:
            desc = item.get('description', '')[:24]
            qty = item.get('quantity', 0)
            subtotal = item.get('subtotal', 0.0)
            line = f"{desc:<25} {str(qty):>7} {f'{subtotal:.2f}':>12}\n"
            cart_text.append(line)
        
        self.cart_textbox.insert("1.0", "".join(cart_text))
        self.cart_textbox.configure(state="disabled")
        self.total_price_label.configure(text=f"الإجمالي الكلي للطلب: {grand_total:.2f} جنيه")
        self.paid_entry.delete(0, 'end')
        self.paid_entry.insert(0, str(grand_total))
    
    def refresh_materials_display(self):
        self.materials_textbox.configure(state="normal")
        self.materials_textbox.delete("1.0", "end")
        if not self.consumed_materials:
            self.materials_textbox.insert("1.0", "لم يتم إضافة مواد مستهلكة.")
        else:
            for item in self.consumed_materials:
                self.materials_textbox.insert("end", f"- {item['name']}: {item['quantity']} {item['unit']}\n")
        self.materials_textbox.configure(state="disabled")

    def add_consumed_material_popup(self):
        conn = sqlite3.connect('receipts.db')
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, unit, stock_level FROM inventory ORDER BY name")
        all_materials = cursor.fetchall()
        conn.close()

        if not all_materials:
            messagebox.showinfo("تنبيه", "لا توجد مواد خام في المخزون. يرجى إضافتها من صفحة إدارة المخزون أولاً.")
            return

        popup = ctk.CTkToplevel(self)
        popup.title("صرف مادة من المخزون")
        popup.geometry("450x300")
        popup.transient(self)
        popup.grab_set()

        material_map = {f"{name} ({stock_level} {unit})": (id, name, unit, stock_level) for id, name, unit, stock_level in all_materials}
        
        ctk.CTkLabel(popup, text="اختر المادة:", font=("Arial", 14)).pack(pady=(15, 5))
        material_var = ctk.StringVar(value=list(material_map.keys())[0])
        ctk.CTkOptionMenu(popup, variable=material_var, values=list(material_map.keys()), width=350).pack()

        ctk.CTkLabel(popup, text="الكمية المصروفة:", font=("Arial", 14)).pack(pady=(10, 5))
        quantity_entry = ctk.CTkEntry(popup, width=300)
        quantity_entry.pack()

        def save_consumption():
            selected_key = material_var.get()
            material_id, name, unit, stock_level = material_map[selected_key]
            
            try:
                quantity = float(quantity_entry.get())
                if quantity <= 0:
                    messagebox.showerror("خطأ", "الكمية يجب أن تكون أكبر من صفر.", parent=popup)
                    return
                if quantity > stock_level:
                    if not messagebox.askyesno("تحذير", f"الكمية المصروفة ({quantity}) أكبر من الكمية المتاحة في المخزون ({stock_level}).\nهل تريد المتابعة على أي حال؟ (سيصبح المخزون بالسالب)", parent=popup):
                        return
            except (ValueError, TypeError):
                messagebox.showerror("خطأ", "الرجاء إدخال كمية رقمية صحيحة.", parent=popup)
                return

            self.consumed_materials.append({
                "id": material_id,
                "name": name,
                "unit": unit,
                "quantity": quantity
            })
            self.refresh_materials_display()
            popup.destroy()

        ctk.CTkButton(popup, text="إضافة وصرف", command=save_consumption).pack(pady=20)

    def finalize_order(self):
        if not self.controller.current_order_items:
            messagebox.showwarning("تنبيه", "لا توجد بنود في الطلب لإنهاء الفاتورة."); return
        try:
            discount = float(self.discount_entry.get() or 0)
            amount_paid = float(self.paid_entry.get() or 0)
        except ValueError:
            messagebox.showerror("خطأ", "الرجاء إدخال أرقام صحيحة."); return
        
        due_date = self.due_date_entry.get() or date.today().strftime('%Y-%m-%d')
        notes = self.notes_entry.get()
        now = datetime.now()
        subtotal = sum(item['subtotal'] for item in self.controller.current_order_items)
        remaining = (subtotal - discount) - amount_paid
        
        receipt_data_dict = {
            "receipt_id": "PREVIEW", "customer_name": self.controller.current_customer_name, "timestamp": now,
            "items": self.controller.current_order_items, "subtotal": subtotal, "discount": discount,
            "paid": amount_paid, "remaining": remaining, "notes": notes, "due_date": due_date
        }
        
        receipt_text_for_db = format_receipt_for_display(**receipt_data_dict)
        
        # <<<--- تعديل: حفظ الفاتورة والمواد في نفس الوقت --- >>>
        receipt_id = None
        conn = None
        try:
            # الخطوة 1: حفظ الفاتورة الأساسية والحصول على ID والاتصال
            receipt_id, conn = self.controller.save_receipt(
                receipt_text_for_db, subtotal, self.controller.current_customer_id, 
                due_date, notes, discount, amount_paid, remaining
            )
            if not receipt_id:
                raise Exception("لم يتم إنشاء الفاتورة بنجاح.")
                
            cursor = conn.cursor()

            # الخطوة 2: تحديث المخزون وحفظ المواد المستخدمة
            if self.consumed_materials:
                for item in self.consumed_materials:
                    # تحديث المخزون
                    cursor.execute("UPDATE inventory SET stock_level = stock_level - ? WHERE id = ?", 
                                   (item['quantity'], item['id']))
                    # حفظ الرابط بين الفاتورة والمادة
                    cursor.execute("INSERT INTO job_materials (receipt_id, inventory_id, quantity_used) VALUES (?, ?, ?)",
                                   (receipt_id, item['id'], item['quantity']))
            
            conn.commit() # تنفيذ كل العمليات معاً
            messagebox.showinfo("نجاح", "تم حفظ الفاتورة وتحديث المخزون بنجاح.")

        except Exception as e:
            if conn:
                conn.rollback() # تراجع عن أي تغييرات إذا حدث خطأ
            messagebox.showerror("خطأ جسيم", f"فشل حفظ الفاتورة. تم التراجع عن كل التغييرات.\nالخطأ: {e}")
            return
        finally:
            if conn:
                conn.close()

        # باقي الكود كما هو
        receipt_data_dict['receipt_id'] = receipt_id
        
        # <<<--- تعديل: إضافة رقم هاتف العميل إلى البيانات قبل عرضها --- >>>
        conn_phone = sqlite3.connect('receipts.db')
        cursor_phone = conn_phone.cursor()
        cursor_phone.execute("SELECT phone FROM customers WHERE id = ?", (self.controller.current_customer_id,))
        customer_phone_tuple = cursor_phone.fetchone()
        conn_phone.close()
        customer_phone = customer_phone_tuple[0] if customer_phone_tuple else ''
        receipt_data_dict['customer_phone'] = customer_phone

        display_text = format_receipt_for_display(**receipt_data_dict)
        
        receipt_page = self.controller.get_frame("Page3_Receipt")
        receipt_page.update_receipt_data(display_text, receipt_data_dict)
        self.controller.show_frame("Page3_Receipt")
        
        self.controller.clear_current_order()
        self.controller.current_customer_id = None
        self.controller.current_customer_name = None

    def cancel_order(self):
        if messagebox.askyesno("تأكيد", "هل أنت متأكد من رغبتك في إلغاء الطلب الحالي بالكامل؟"):
            self.controller.clear_current_order()
            self.controller.current_customer_id = None
            self.controller.current_customer_name = None
            messagebox.showinfo("تم الإلغاء", "تم إلغاء الطلب الحالي.")
            self.controller.show_frame("Page_CustomerSelection")

# ... (كلاس Page3_Receipt يبقى كما هو) ...
class Page3_Receipt(ctk.CTkFrame):
    def __init__(self, parent, controller):
        super().__init__(parent)
        self.controller = controller
        self.receipt_data = {}
        ctk.CTkLabel(self, text="معاينة الفاتورة قبل الطباعة", font=ctk.CTkFont(size=24, weight="bold")).pack(pady=20)
        self.bill_textbox = ctk.CTkTextbox(self, font=("Courier New", 14), state="disabled")
        self.bill_textbox.pack(fill="both", expand=True, padx=20, pady=10)
        
        bottom_button_frame = ctk.CTkFrame(self, fg_color="transparent")
        bottom_button_frame.pack(pady=20, fill="x", padx=50)
        
        ctk.CTkButton(bottom_button_frame, text="طلب جديد", font=("Arial", 16, "bold"), height=40,
                      command=lambda: self.controller.show_frame("Page_CustomerSelection")).pack(side="right", expand=True, padx=5)
        
        # --- الزر الجديد لحفظ PDF بجودة عالية ---
        ctk.CTkButton(bottom_button_frame, text="حفظ كـ PDF", font=("Arial", 16, "bold"), height=40,
                      fg_color="#e74c3c", hover_color="#c0392b", command=self.save_as_pdf).pack(side="right", expand=True, padx=5)
        
        ctk.CTkButton(bottom_button_frame, text="حفظ كصورة", font=("Arial", 16, "bold"), height=40,
                      fg_color="#3498db", hover_color="#2980b9", command=self.save_as_image).pack(side="right", expand=True, padx=5)
        
        ctk.CTkButton(bottom_button_frame, text="حفظ كملف نصي", font=("Arial", 16, "bold"), height=40,
                      fg_color="#f39c12", hover_color="#e67e22", command=self.save_as_txt).pack(side="right", expand=True, padx=5)
        
        ctk.CTkButton(bottom_button_frame, text="طباعة إيصال حراري", font=("Arial", 16, "bold"), height=40,
                      fg_color="#27ae60", hover_color="#2ecc71", command=self.print_receipt).pack(side="left", expand=True, padx=5)

    def update_receipt_data(self, content, data):
        self.bill_textbox.configure(state="normal")
        self.bill_textbox.delete("1.0", "end")
        self.bill_textbox.insert("1.0", content)
        self.bill_textbox.configure(state="disabled")
        self.receipt_data = data
    
    def save_as_txt(self):
        if not self.receipt_data:
            messagebox.showwarning("تنبيه", "لا توجد بيانات فاتورة لحفظها.")
            return
        try:
            filename = f"Receipt_{self.receipt_data['receipt_id']}.txt"
            receipt_text_content = self.bill_textbox.get("1.0", "end-1c")
            with open(filename, 'w', encoding='utf-8') as f:
                f.write(receipt_text_content)
            messagebox.showinfo("نجاح", f"تم حفظ الفاتورة كملف نصي باسم:\n{filename}")
            os.startfile(filename)
        except Exception as e:
            messagebox.showerror("خطأ", f"فشلت عملية حفظ الملف النصي.\nالخطأ: {e}")

    def save_as_image(self):
        if not self.receipt_data:
            messagebox.showwarning("تنبيه", "لا توجد بيانات فاتورة لحفظها.")
            return
        try:
            filename = f"Preview_Receipt_{self.receipt_data['receipt_id']}.png"
            generate_preview_image(filename, self.receipt_data)
            messagebox.showinfo("نجاح", f"تم حفظ معاينة الفاتورة كصورة باسم:\n{filename}")
        except Exception as e:
            messagebox.showerror("خطأ", f"فشلت عملية حفظ الصورة.\nالخطأ: {e}")

    # --- الدالة الجديدة التي سيستدعيها الزر الجديد ---
    def save_as_pdf(self):
        if not self.receipt_data:
            messagebox.showwarning("تنبيه", "لا توجد بيانات فاتورة لحفظها.")
            return
        try:
            filename = f"Invoice_{self.receipt_data['receipt_id']}.pdf"
            generate_pdf_receipt(filename, self.receipt_data)
            messagebox.showinfo("نجاح", f"تم حفظ الفاتورة كملف PDF باسم:\n{filename}")
            os.startfile(filename) # فتح الملف تلقائياً
        except Exception as e:
            messagebox.showerror("خطأ", f"فشلت عملية حفظ ملف PDF.\nالخطأ: {e}")

    def print_receipt(self):
        if not self.receipt_data:
            messagebox.showwarning("تنبيه", "لا توجد بيانات فاتورة لطباعتها.")
            return
        try:
            from escpos.printer import Windows
            PRINTER_NAME = "POS-80"
            printer = Windows(printer_name=PRINTER_NAME)
            print_escpos_receipt(printer, self.receipt_data)
        except ImportError:
             messagebox.showerror("خطأ", "مكتبة python-escpos غير مثبتة. لا يمكن الطباعة.")
        except Exception as e:
            messagebox.showerror(
                "خطأ طباعة", 
                f"فشلت عملية الطباعة. تأكد أن الطابعة مركبة في ويندوز وأن الاسم '{PRINTER_NAME}' صحيح.\n\nالخطأ: {e}"
            )
# <<<--- تم التعديل على هذا الكلاس بالكامل (AdminDashboard) --- >>>
class AdminDashboard(ctk.CTkFrame):
    def __init__(self, parent, controller):
        super().__init__(parent)
        self.controller = controller
        self.grid_columnconfigure((0, 1, 2, 3), weight=1)
        self.grid_rowconfigure(1, weight=1)
        
        ctk.CTkLabel(self, text="لوحة التحكم الرئيسية", font=ctk.CTkFont(size=24, weight="bold")).grid(row=0, column=0, columnspan=4, padx=20, pady=20)
        
        self.income_card = self.create_summary_card("الدخل اليومي", "0.00 جنيه", "#2ECC71", 0, 0)
        self.expense_card = self.create_summary_card("المصروفات اليومية", "0.00 جنيه", "#E74C3C", 0, 1)
        self.profit_card = self.create_summary_card("الربح اليومي", "0.00 جنيه", "#3498DB", 0, 2)
        self.debt_card = self.create_summary_card("إجمالي الديون", "0.00 جنيه", "#F39C12", 0, 3)
        
        actions_frame = ctk.CTkFrame(self)
        actions_frame.grid(row=2, column=0, columnspan=4, pady=20)
        
        ctk.CTkButton(actions_frame, text="إضافة مصروفات", fg_color="#e67e22", hover_color="#d35400", command=self.add_expense_popup).pack(side="right", padx=10)
        ctk.CTkButton(actions_frame, text="تحديث اللوحة", command=self.load_daily_summary).pack(side="right", padx=10)
        
        # <<<--- تعديل: إضافة زر التقرير الشامل للأدمن فقط --- >>>
        if self.controller.current_user == 'admin':
            ctk.CTkButton(actions_frame, text="📊 تصدير تقرير شامل (Excel)", height=40, font=("Arial", 14, "bold"),
                          fg_color="#16A085", hover_color="#1ABC9C", 
                          command=self.create_admin_report_popup).pack(side="left", padx=20)

    def create_summary_card(self, title, initial_value, color, row, col):
        card = ctk.CTkFrame(self, fg_color=color, corner_radius=10)
        card.grid(row=row+1, column=col, padx=10, pady=10, sticky="nsew")
        ctk.CTkLabel(card, text=title, font=ctk.CTkFont(size=16)).pack(pady=(20, 10))
        value_label = ctk.CTkLabel(card, text=initial_value, font=ctk.CTkFont(size=28, weight="bold"))
        value_label.pack(pady=(0, 20), padx=20)
        return value_label
        
    def load_daily_summary(self):
        conn = sqlite3.connect('receipts.db')
        cursor = conn.cursor()
        cursor.execute("SELECT SUM(amount_paid) FROM receipts WHERE date(timestamp) = date('now', 'localtime')")
        total_income = cursor.fetchone()[0] or 0
        cursor.execute("SELECT SUM(amount) FROM expenses WHERE date(timestamp) = date('now', 'localtime')")
        total_expenses = cursor.fetchone()[0] or 0
        cursor.execute("SELECT SUM(remaining_amount) FROM receipts")
        total_debt = cursor.fetchone()[0] or 0
        conn.close()
        net_profit = total_income - total_expenses
        self.income_card.configure(text=f"{total_income:.2f} جنيه")
        self.expense_card.configure(text=f"{total_expenses:.2f} جنيه")
        self.profit_card.configure(text=f"{net_profit:.2f} جنيه")
        self.debt_card.configure(text=f"{total_debt:.2f} جنيه")
        
    def add_expense_popup(self):
        popup = ctk.CTkToplevel(self)
        popup.title("إضافة مصروف جديد")
        popup.geometry("400x250")
        popup.transient(self)
        popup.grab_set()
        ctk.CTkLabel(popup, text="وصف المصروف:", font=("Arial", 14)).pack(pady=(15, 5))
        desc_entry = ctk.CTkEntry(popup, width=300)
        desc_entry.pack()
        ctk.CTkLabel(popup, text="المبلغ:", font=("Arial", 14)).pack(pady=(10, 5))
        amount_entry = ctk.CTkEntry(popup, width=300)
        amount_entry.pack()
        def save_expense():
            desc = desc_entry.get()
            amount_str = amount_entry.get()
            if not desc or not amount_str:
                messagebox.showerror("خطأ", "الرجاء ملء كل الخانات.", parent=popup); return
            try:
                amount = float(amount_str)
                if amount <= 0: raise ValueError
            except ValueError:
                messagebox.showerror("خطأ", "الرجاء إدخال مبلغ صحيح.", parent=popup); return
            conn = sqlite3.connect('receipts.db')
            cursor = conn.cursor()
            cursor.execute("INSERT INTO expenses (timestamp, description, amount) VALUES (?, ?, ?)",
                           (datetime.now(), desc, amount))
            conn.commit()
            conn.close()
            messagebox.showinfo("نجاح", "تم حفظ المصروف بنجاح.", parent=popup)
            popup.destroy()
            self.load_daily_summary()
        ctk.CTkButton(popup, text="حفظ المصروف", command=save_expense).pack(pady=20)

    # <<<--- تعديل: الدالة الجديدة لإنشاء نافذة تحديد تاريخ التقرير --- >>>
    def create_admin_report_popup(self):
        popup = ctk.CTkToplevel(self)
        popup.title("تحديد فترة التقرير")
        popup.geometry("400x250")
        popup.transient(self)
        popup.grab_set()

        today = date.today()
        first_day_of_month = today.replace(day=1)

        ctk.CTkLabel(popup, text="تاريخ البدء (YYYY-MM-DD):", font=("Arial", 14)).pack(pady=(15, 5))
        start_date_entry = ctk.CTkEntry(popup, width=300)
        start_date_entry.insert(0, first_day_of_month.strftime('%Y-%m-%d'))
        start_date_entry.pack()
        
        ctk.CTkLabel(popup, text="تاريخ الانتهاء (YYYY-MM-DD):", font=("Arial", 14)).pack(pady=(10, 5))
        end_date_entry = ctk.CTkEntry(popup, width=300)
        end_date_entry.insert(0, today.strftime('%Y-%m-%d'))
        end_date_entry.pack()
        
        def generate():
            start_date_str = start_date_entry.get()
            end_date_str = end_date_entry.get()
            try:
                # أضف ساعة النهاية لضمان شمول اليوم بأكمله
                start_date = datetime.strptime(start_date_str, '%Y-%m-%d')
                end_date = datetime.strptime(end_date_str, '%Y-%m-%d').replace(hour=23, minute=59, second=59)
                popup.destroy()
                self.export_admin_report(start_date, end_date)
            except ValueError:
                messagebox.showerror("خطأ", "الرجاء إدخال التاريخ بالصيغة الصحيحة YYYY-MM-DD.", parent=popup)

        ctk.CTkButton(popup, text="إنشاء التقرير", command=generate).pack(pady=20)
    
    # <<<--- تعديل: الدالة الرئيسية لتوليد التقرير الشامل --- >>>
    def export_admin_report(self, start_date, end_date):
        try:
            from xlsxwriter.utility import xl_rowcol_to_cell
            # الخطوة 1: جلب كل البيانات المطلوبة
            conn = sqlite3.connect('receipts.db')
            query = """
                SELECT
                    r.id as 'رقم الفاتورة',
                    strftime('%Y-%m-%d %H:%M', r.timestamp) as 'تاريخ الفاتورة',
                    c.name as 'اسم العميل',
                    r.total_amount as 'إجمالي الفاتورة',
                    r.discount as 'الخصم',
                    (r.total_amount - r.discount) as 'الصافي',
                    r.amount_paid as 'المدفوع',
                    r.remaining_amount as 'المتبقي',
                    i.name as 'المادة المستخدمة',
                    jm.quantity_used as 'الكمية المستخدمة',
                    i.purchase_price as 'سعر شراء الوحدة',
                    (jm.quantity_used * i.purchase_price) as 'تكلفة المادة'
                FROM receipts r
                JOIN customers c ON r.customer_id = c.id
                LEFT JOIN job_materials jm ON r.id = jm.receipt_id
                LEFT JOIN inventory i ON jm.inventory_id = i.id
                WHERE r.timestamp BETWEEN ? AND ?
                ORDER BY r.timestamp DESC;
            """
            df = pd.read_sql_query(query, conn, params=(start_date, end_date))
            
            # جلب بيانات المصروفات
            expenses_df = pd.read_sql_query("""
                SELECT strftime('%Y-%m-%d', timestamp) as 'التاريخ', description as 'البيان', amount as 'المبلغ' 
                FROM expenses WHERE timestamp BETWEEN ? AND ?
            """, conn, params=(start_date, end_date))

            # الخطوة 2: معالجة البيانات وحساب الربحية
            if not df.empty:
                df_jobs = df.groupby('رقم الفاتورة').agg({
                    'تاريخ الفاتورة': 'first',
                    'اسم العميل': 'first',
                    'إجمالي الفاتورة': 'first',
                    'الخصم': 'first',
                    'الصافي': 'first',
                    'المدفوع': 'first',
                    'المتبقي': 'first',
                    'تكلفة المادة': 'sum'
                }).reset_index()
                df_jobs.rename(columns={'تكلفة المادة': 'إجمالي تكلفة المواد'}, inplace=True)
                df_jobs['الربح الصافي'] = df_jobs['الصافي'] - df_jobs['إجمالي تكلفة المواد']
            else:
                df_jobs = pd.DataFrame()

            # الخطوة 3: تحليل أفضل العملاء
            now = datetime.now()
            # الأسبوع الحالي
            start_week = now - timedelta(days=now.weekday())
            top_customers_week_df = pd.read_sql_query("""
                SELECT c.name, SUM(r.total_amount - r.discount) as total
                FROM receipts r JOIN customers c ON r.customer_id = c.id
                WHERE r.timestamp >= ? GROUP BY c.id ORDER BY total DESC LIMIT 10
            """, conn, params=(start_week,))
            # الشهر الحالي
            start_month = now.replace(day=1)
            top_customers_month_df = pd.read_sql_query("""
                SELECT c.name, SUM(r.total_amount - r.discount) as total
                FROM receipts r JOIN customers c ON r.customer_id = c.id
                WHERE r.timestamp >= ? GROUP BY c.id ORDER BY total DESC LIMIT 10
            """, conn, params=(start_month,))
            # السنة الحالية
            start_year = now.replace(day=1, month=1)
            top_customers_year_df = pd.read_sql_query("""
                SELECT c.name, SUM(r.total_amount - r.discount) as total
                FROM receipts r JOIN customers c ON r.customer_id = c.id
                WHERE r.timestamp >= ? GROUP BY c.id ORDER BY total DESC LIMIT 10
            """, conn, params=(start_year,))

            conn.close()

            # الخطوة 4: كتابة البيانات إلى ملف Excel منسق
            filename = f"Admin_Report_{start_date.strftime('%Y-%m-%d')}_to_{end_date.strftime('%Y-%m-%d')}.xlsx"
            with pd.ExcelWriter(filename, engine='xlsxwriter') as writer:
                workbook = writer.book
                
                # تنسيقات الخلايا
                header_format = workbook.add_format({'bold': True, 'text_wrap': True, 'valign': 'top', 'fg_color': '#4F81BD', 'font_color': 'white', 'border': 1, 'align': 'center'})
                currency_format = workbook.add_format({'num_format': '#,##0.00 "ج.م"', 'border': 1})
                default_format = workbook.add_format({'border': 1})

                # --- ورقة الملخص ---
                total_income = df_jobs['الصافي'].sum() if not df_jobs.empty else 0
                total_material_cost = df_jobs['إجمالي تكلفة المواد'].sum() if not df_jobs.empty else 0
                total_expenses = expenses_df['المبلغ'].sum() if not expenses_df.empty else 0
                total_profit = total_income - total_material_cost - total_expenses
                
                summary_sheet = workbook.add_worksheet('ملخص التقرير')
                summary_sheet.right_to_left()
                title_format = workbook.add_format({'bold': True, 'font_size': 16})
                label_format = workbook.add_format({'bold': True, 'font_size': 12})
                value_format = workbook.add_format({'font_size': 12, 'num_format': '#,##0.00 "ج.م"'})
                
                summary_sheet.write('C2', 'ملخص مالي للفترة', title_format)
                summary_sheet.write('C4', 'إجمالي الدخل (بعد الخصم):', label_format)
                summary_sheet.write('B4', total_income, value_format)
                summary_sheet.write('C5', 'إجمالي تكلفة المواد الخام:', label_format)
                summary_sheet.write('B5', total_material_cost, value_format)
                summary_sheet.write('C6', 'إجمالي المصروفات الأخرى:', label_format)
                summary_sheet.write('B6', total_expenses, value_format)
                summary_sheet.write('C7', 'صافي الربح الإجمالي:', label_format)
                summary_sheet.write('B7', total_profit, value_format)
                summary_sheet.set_column('B:C', 25)

                # --- ورقة الشغلانات ---
                if not df_jobs.empty:
                    df_jobs.to_excel(writer, sheet_name='تقرير الشغلانات', index=False, startrow=1)
                    jobs_sheet = writer.sheets['تقرير الشغلانات']
                    jobs_sheet.right_to_left()
                    for col_num, value in enumerate(df_jobs.columns.values):
                        jobs_sheet.write(0, col_num, value, header_format)
                    jobs_sheet.set_column('A:A', 10, default_format)
                    jobs_sheet.set_column('B:B', 18, default_format)
                    jobs_sheet.set_column('C:C', 25, default_format)
                    jobs_sheet.set_column('D:K', 15, currency_format)

                # --- ورقة تفاصيل المواد ---
                if not df.empty:
                    df_materials = df[['رقم الفاتورة', 'المادة المستخدمة', 'الكمية المستخدمة', 'سعر شراء الوحدة', 'تكلفة المادة']].dropna()
                    df_materials.to_excel(writer, sheet_name='تفاصيل المواد المستخدمة', index=False, startrow=1)
                    mat_sheet = writer.sheets['تفاصيل المواد المستخدمة']
                    mat_sheet.right_to_left()
                    for col_num, value in enumerate(df_materials.columns.values):
                        mat_sheet.write(0, col_num, value, header_format)
                    
                # --- ورقة المصروفات ---
                if not expenses_df.empty:
                    expenses_df.to_excel(writer, sheet_name='المصروفات', index=False, startrow=1)
                    exp_sheet = writer.sheets['المصروفات']
                    exp_sheet.right_to_left()
                    for col_num, value in enumerate(expenses_df.columns.values):
                        exp_sheet.write(0, col_num, value, header_format)
                
                # --- ورقة أفضل العملاء ---
                top_sheet = workbook.add_worksheet('أفضل العملاء')
                top_sheet.right_to_left()
                top_sheet.write(0, 5, "أفضل العملاء (خلال العام)", header_format)
                top_sheet.write(0, 3, "أفضل العملاء (خلال الشهر)", header_format)
                top_sheet.write(0, 1, "أفضل العملاء (خلال الأسبوع)", header_format)
                top_customers_week_df.to_excel(writer, sheet_name='أفضل العملاء', header=["العميل", "إجمالي التعامل"], index=False, startrow=1, startcol=0)
                top_customers_month_df.to_excel(writer, sheet_name='أفضل العملاء', header=["العميل", "إجمالي التعامل"], index=False, startrow=1, startcol=2)
                top_customers_year_df.to_excel(writer, sheet_name='أفضل العملاء', header=["العميل", "إجمالي التعامل"], index=False, startrow=1, startcol=4)
                top_sheet.set_column('A:F', 20)
                
            messagebox.showinfo("نجاح", f"تم إنشاء التقرير الشامل بنجاح!\nتم حفظه باسم: {filename}")
            os.startfile(filename)

        except ImportError:
            messagebox.showerror("خطأ", "مكتبة 'xlsxwriter' غير مثبتة. يرجى تثبيتها أولاً:\npip install xlsxwriter")
        except Exception as e:
            messagebox.showerror("خطأ غير متوقع", f"حدث خطأ أثناء إنشاء التقرير: {e}")

# ... (كلاس Page_Analysis يبقى كما هو) ...

class Page_Analysis(ctk.CTkFrame):
    def __init__(self, parent, controller):
        super().__init__(parent)
        self.controller = controller
        self.analysis_df = None
        self.grid_rowconfigure(2, weight=1)
        self.grid_columnconfigure(1, weight=1)
        
        self.ARABIC_MONTHS = {
            "01": "يناير", "02": "فبراير", "03": "مارس", "04": "أبريل",
            "05": "مايو", "06": "يونيو", "07": "يوليو", "08": "أغسطس",
            "09": "سبتمبر", "10": "أكتوبر", "11": "نوفمبر", "12": "ديسمبر"
        }
        
        top_frame = ctk.CTkFrame(self, fg_color="transparent")
        top_frame.grid(row=0, column=0, columnspan=2, pady=10, sticky="ew")
        ctk.CTkLabel(top_frame, text="التحليل المالي", font=ctk.CTkFont(size=24, weight="bold")).pack()
        controls_frame = ctk.CTkFrame(top_frame, fg_color="transparent")
        controls_frame.pack(pady=10)
        self.year_var = ctk.StringVar(value="اختر السنة")
        self.year_menu = ctk.CTkOptionMenu(controls_frame, variable=self.year_var, values=["اختر السنة"])
        self.year_menu.pack(side="right", padx=10)
        self.month_var = ctk.StringVar(value="كل الشهور")
        months = ["كل الشهور"] + [str(i) for i in range(1, 13)]
        self.month_menu = ctk.CTkOptionMenu(controls_frame, variable=self.month_var, values=months)
        self.month_menu.pack(side="right", padx=10)
        ctk.CTkButton(controls_frame, text="عرض التحليل", command=self.generate_analysis).pack(side="left", padx=10)
        self.export_button = ctk.CTkButton(controls_frame, text="تصدير مالي (Excel)", state="disabled", command=self.export_to_excel)
        self.export_button.pack(side="left", padx=10)
        self.product_export_button = ctk.CTkButton(controls_frame, text="تصدير منتجات (Excel)", fg_color="#16A085", hover_color="#1ABC9C",
                                                   state="disabled", command=self.export_product_analysis)
        self.product_export_button.pack(side="left", padx=10)
        left_panel = ctk.CTkFrame(self)
        left_panel.grid(row=1, column=0, rowspan=2, sticky="nsew", padx=10, pady=10)
        left_panel.grid_rowconfigure(0, weight=1)
        left_panel.grid_rowconfigure(1, weight=1)
        left_panel.grid_columnconfigure(0, weight=1)
        self.analysis_textbox = ctk.CTkTextbox(left_panel, font=("Courier New", 12))
        self.analysis_textbox.grid(row=0, column=0, sticky="nsew", pady=5)
        self.pie_chart_frame = ctk.CTkFrame(left_panel, fg_color="gray20")
        self.pie_chart_frame.grid(row=1, column=0, sticky="nsew", pady=5)
        self.bar_chart_frame = ctk.CTkFrame(self, fg_color="gray20")
        self.bar_chart_frame.grid(row=1, column=1, sticky="nsew", padx=10, pady=10)
    def clear_charts(self):
        for widget in self.bar_chart_frame.winfo_children(): widget.destroy()
        for widget in self.pie_chart_frame.winfo_children(): widget.destroy()
    def populate_year_selector(self):
        conn = sqlite3.connect('receipts.db')
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT strftime('%Y', timestamp) FROM receipts UNION SELECT DISTINCT strftime('%Y', timestamp) FROM expenses")
        years = [row[0] for row in cursor.fetchall() if row[0]]
        conn.close()
        if years:
            sorted_years = sorted(years, reverse=True)
            self.year_menu.configure(values=sorted_years)
            self.year_var.set(sorted_years[0])
        else:
            self.year_menu.configure(values=["لا توجد بيانات"])
            self.year_var.set("لا توجد بيانات")
    def generate_analysis(self):
        selected_year = self.year_var.get()
        selected_month = self.month_var.get()
        if selected_year in ["اختر السنة", "لا توجد بيانات"]:
            messagebox.showwarning("تنبيه", "الرجاء اختيار سنة لعرض تحليلها."); return
        if selected_month == "كل الشهور":
            self.generate_yearly_analysis(selected_year)
        else:
            self.generate_monthly_analysis(selected_year, selected_month)
    def generate_yearly_analysis(self, year):
        conn = sqlite3.connect('receipts.db')
        income_query = f"SELECT strftime('%m', timestamp) as month, SUM(total_amount) as income FROM receipts WHERE strftime('%Y', timestamp) = '{year}' GROUP BY month"
        expenses_query = f"SELECT strftime('%m', timestamp) as month, SUM(amount) as expenses FROM expenses WHERE strftime('%Y', timestamp) = '{year}' GROUP BY month"
        income_df = pd.read_sql_query(income_query, conn)
        expenses_df = pd.read_sql_query(expenses_query, conn)
        conn.close()
        df = pd.DataFrame({'month': [f"{i:02d}" for i in range(1, 13)]})
        df = pd.merge(df, income_df, on='month', how='left').fillna(0)
        df = pd.merge(df, expenses_df, on='month', how='left').fillna(0)
        df['profit'] = df['income'] - df['expenses']
        df['month_name_ar'] = df['month'].apply(lambda x: self.ARABIC_MONTHS.get(x, ''))
        self.analysis_df = df
        self.export_button.configure(state="normal")
        self.product_export_button.configure(state="normal")
        report_text = f"التحليل المالي المفصل لسنة: {year}\n\n"
        header = f"| {'الشهر'.center(12)} | {'إجمالي الدخل'.center(20)} | {'إجمالي المصروفات'.center(20)} | {'صافي الربح'.center(20)} |\n"
        separator = "-" * (len(header) - 1) + "\n"
        report_text += separator + header + separator
        for index, row in df.iterrows():
            report_text += f"| {row['month_name_ar'].ljust(12)} | {f'{row.income:.2f} ج'.center(20)} | {f'{row.expenses:.2f} ج'.center(20)} | {f'{row.profit:.2f} ج'.center(20)} |\n"
        total_income, total_expenses, total_profit = df.income.sum(), df.expenses.sum(), df.profit.sum()
        report_text += separator
        footer = f"| {'الإجمالي السنوي'.ljust(12)} | {f'{total_income:.2f} ج'.center(20)} | {f'{total_expenses:.2f} ج'.center(20)} | {f'{total_profit:.2f} ج'.center(20)} |\n"
        report_text += footer + separator
        self.analysis_textbox.configure(state="normal")
        self.analysis_textbox.delete("1.0", "end")
        self.analysis_textbox.insert("1.0", report_text)
        self.analysis_textbox.configure(state="disabled")
        self.clear_charts()
        self.create_bar_chart(df, 'month_name_ar', 'الدخل والمصروفات الشهرية')
        self.create_pie_chart(total_profit, total_expenses)
    def generate_monthly_analysis(self, year, month):
        month_int = int(month)
        num_days = calendar.monthrange(int(year), month_int)[1]
        conn = sqlite3.connect('receipts.db')
        income_query = f"SELECT strftime('%d', timestamp) as day, SUM(total_amount) as income FROM receipts WHERE strftime('%Y-%m', timestamp) = '{year}-{month_int:02d}' GROUP BY day"
        expenses_query = f"SELECT strftime('%d', timestamp) as day, SUM(amount) as expenses FROM expenses WHERE strftime('%Y-%m', timestamp) = '{year}-{month_int:02d}' GROUP BY day"
        income_df = pd.read_sql_query(income_query, conn)
        expenses_df = pd.read_sql_query(expenses_query, conn)
        conn.close()
        df = pd.DataFrame({'day': [f"{i:02d}" for i in range(1, num_days + 1)]})
        df = pd.merge(df, income_df, on='day', how='left').fillna(0)
        df = pd.merge(df, expenses_df, on='day', how='left').fillna(0)
        df['profit'] = df['income'] - df['expenses']
        self.analysis_df = df
        self.export_button.configure(state="normal")
        self.product_export_button.configure(state="normal")
        month_name = self.ARABIC_MONTHS.get(f"{month_int:02d}")
        report_text = f"التحليل المالي المفصل لشهر: {month_name} {year}\n\n"
        header = f"| {'اليوم'.center(10)} | {'إجمالي الدخل'.center(20)} | {'إجمالي المصروفات'.center(20)} | {'صافي الربح'.center(20)} |\n"
        separator = "-" * (len(header) - 1) + "\n"
        report_text += separator + header + separator
        for index, row in df.iterrows():
            report_text += f"| {row['day'].center(10)} | {f'{row.income:.2f} ج'.center(20)} | {f'{row.expenses:.2f} ج'.center(20)} | {f'{row.profit:.2f} ج'.center(20)} |\n"
        total_income, total_expenses, total_profit = df.income.sum(), df.expenses.sum(), df.profit.sum()
        report_text += separator
        footer = f"| {'الإجمالي الشهري'.ljust(10)} | {f'{total_income:.2f} ج'.center(20)} | {f'{total_expenses:.2f} ج'.center(20)} | {f'{total_profit:.2f} ج'.center(20)} |\n"
        report_text += footer + separator
        self.analysis_textbox.configure(state="normal")
        self.analysis_textbox.delete("1.0", "end")
        self.analysis_textbox.insert("1.0", report_text)
        self.analysis_textbox.configure(state="disabled")
        self.clear_charts()
        self.create_bar_chart(df, 'day', f'الدخل والمصروفات اليومية لشهر {month_name}')
        self.create_pie_chart(total_profit, total_expenses)
    def create_bar_chart(self, df, x_axis, title):
        plt.rcParams['font.family'] = 'Arial'
        plt.rcParams['axes.unicode_minus'] = False
        plt.style.use('seaborn-v0_8-darkgrid')
        fig, ax = plt.subplots(figsize=(8, 4))
        fig.patch.set_facecolor('#2B2B2B')
        ax.set_facecolor('#3C3F41')
        ax.bar(df[x_axis], df['income'], color='#2ECC71', label='الدخل')
        ax.bar(df[x_axis], df['expenses'], color='#E74C3C', label='المصروفات', width=0.5)
        ax.set_title(title, color='white')
        ax.set_ylabel('المبلغ (ج.م)', color='white')
        ax.tick_params(axis='x', labelrotation=45, colors='white')
        ax.tick_params(axis='y', colors='white')
        ax.legend()
        fig.tight_layout()
        canvas = FigureCanvasTkAgg(fig, master=self.bar_chart_frame)
        canvas.draw()
        canvas.get_tk_widget().pack(fill="both", expand=True)
    def create_pie_chart(self, total_profit, total_expenses):
        if total_profit <= 0 and total_expenses <= 0: return
        plt.rcParams['font.family'] = 'Arial'
        if total_profit > 0:
            sizes = [total_profit, total_expenses]
            labels = ['صافي الربح', 'المصروفات']
            colors = ['#3498DB', '#E74C3C']
        else:
            sizes = [abs(total_profit), total_expenses]
            labels = ['صافي الخسارة', 'المصروفات']
            colors = ['#F1C40F', '#E74C3C']
        fig, ax = plt.subplots(figsize=(4, 3))
        fig.patch.set_facecolor('#2B2B2B')
        ax.pie(sizes, labels=labels, autopct='%1.1f%%', startangle=90, colors=colors,
               textprops={'color': "w"}, wedgeprops={'edgecolor': 'white'})
        ax.axis('equal')
        ax.set_title('نسبة الربح إلى المصروفات', color='white')
        canvas = FigureCanvasTkAgg(fig, master=self.pie_chart_frame)
        canvas.draw()
        canvas.get_tk_widget().pack(fill="both", expand=True)
    def export_to_excel(self):
        if self.analysis_df is None:
            messagebox.showwarning("تنبيه", "يجب عرض التحليل أولاً قبل التصدير."); return
        try:
            filename = f"Analysis_Year_{self.year_var.get()}.xlsx"
            df_to_export = self.analysis_df.rename(columns={
                'month_name_ar': 'الشهر', 'income': 'الدخل',
                'expenses': 'المصروفات', 'profit': 'صافي الربح', 'day': 'اليوم'
            })
            if 'الشهر' in df_to_export.columns:
                columns_to_export = ['الشهر', 'الدخل', 'المصروفات', 'صافي الربح']
            else:
                columns_to_export = ['اليوم', 'الدخل', 'المصروفات', 'صافي الربح']

            df_to_export.to_excel(filename, index=False, sheet_name=f"تحليل {self.year_var.get()}",
                                  columns=columns_to_export)
            messagebox.showinfo("نجاح", f"تم تصدير الملف بنجاح إلى:\n{os.path.abspath(filename)}")
        except PermissionError:
            messagebox.showerror("خطأ", "لا يمكن حفظ الملف. قد يكون الملف مفتوحاً في برنامج آخر.")
        except Exception as e:
            messagebox.showerror("خطأ غير متوقع", f"حدث خطأ أثناء التصدير: {e}")
            
    def export_product_analysis(self):
        selected_year = self.year_var.get()
        selected_month = self.month_var.get()
        if selected_year in ["اختر السنة", "لا توجد بيانات"]:
            messagebox.showwarning("تنبيه", "الرجاء اختيار سنة أولاً."); return
        conn = sqlite3.connect('receipts.db')
        query = f"SELECT receipt_data FROM receipts WHERE strftime('%Y', timestamp) = '{selected_year}'"
        if selected_month != "كل الشهور":
            query += f" AND strftime('%m', timestamp) = '{int(selected_month):02d}'"
        df = pd.read_sql_query(query, conn)
        conn.close()
        if df.empty:
            messagebox.showinfo("لا توجد بيانات", f"لا توجد فواتير مسجلة في هذه الفترة."); return
        product_sales = {}
        for _, row in df.iterrows():
            lines = row['receipt_data'].split('\n')
            item_section = False
            for line in lines:
                if "الصنف" in line and "الكمية" in line:
                    item_section = True
                    continue
                if "====" in line and item_section:
                    item_section = False
                    break
                if item_section and line.strip() and "----" not in line:
                    parts = [p.strip() for p in line.split(' ') if p]
                    if len(parts) >= 4:
                        try:
                            item_name = ' '.join(parts[:-3])
                            subtotal = float(parts[-1])
                            
                            if item_name in product_sales:
                                product_sales[item_name]['count'] += 1
                                product_sales[item_name]['total_value'] += subtotal
                            else:
                                product_sales[item_name] = {'count': 1, 'total_value': subtotal}
                        except (ValueError, IndexError):
                            continue
        
        if not product_sales:
            messagebox.showinfo("لا توجد بيانات", "لم يتم العثور على بنود منتجات داخل الفواتير."); return
        sales_df = pd.DataFrame.from_dict(product_sales, orient='index')
        sales_df.index.name = 'المنتج'
        sales_df.rename(columns={'count': 'عدد مرات البيع', 'total_value': 'إجمالي الدخل'}, inplace=True)
        sales_df.sort_values(by='إجمالي الدخل', ascending=False, inplace=True)
        period_str = f"{selected_year}_{selected_month}" if selected_month != "كل الشهور" else selected_year
        filename = f"Product_Analysis_{period_str}.xlsx"
        try:
            with pd.ExcelWriter(filename, engine='xlsxwriter') as writer:
                workbook = writer.book
                header_format = workbook.add_format({'bold': True, 'font_color': 'white', 'bg_color': '#4F81BD', 'align': 'center', 'valign': 'vcenter', 'border': 1})
                currency_format = workbook.add_format({'num_format': '#,##0.00 "جنيه"', 'border': 1})
                default_format = workbook.add_format({'border': 1})
                
                sales_df.to_excel(writer, sheet_name='ملخص أداء المنتجات')
                worksheet = writer.sheets['ملخص أداء المنتجات']
                worksheet.set_column('A:A', 30, default_format)
                worksheet.set_column('B:B', 15, default_format)
                worksheet.set_column('C:C', 20, currency_format)
                for col_num, value in enumerate(sales_df.reset_index().columns.values):
                    worksheet.write(0, col_num, value, header_format)
                
                chart = workbook.add_chart({'type': 'bar'})
                chart.add_series({
                    'name':       ['ملخص أداء المنتجات', 0, 2],
                    'categories': ['ملخص أداء المنتجات', 1, 0, len(sales_df.head(10)), 0],
                    'values':     ['ملخص أداء المنتجات', 1, 2, len(sales_df.head(10)), 2],
                })
                chart.set_title({'name': 'أفضل 10 منتجات من حيث الدخل'})
                chart.set_x_axis({'name': 'المنتج'})
                chart.set_y_axis({'name': 'إجمالي الدخل'})
                worksheet.insert_chart('E2', chart)

            messagebox.showinfo("نجاح", f"تم تصدير تقرير أداء المنتجات بنجاح إلى:\n{os.path.abspath(filename)}")
        except Exception as e:
            messagebox.showerror("خطأ", f"حدث خطأ أثناء التصدير: {e}")

# ... (كلاسات Page_CustomerManagement, Page_JobTracking, Page_DebtsTracking تبقى كما هي) ...

class Page_CustomerManagement(ctk.CTkFrame):
    def __init__(self, parent, controller):
        super().__init__(parent)
        self.controller = controller
        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=2)
        self.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(self, text="إدارة العملاء", font=ctk.CTkFont(size=24, weight="bold")).grid(row=0, column=0, columnspan=2, pady=20)
        left_panel = ctk.CTkFrame(self)
        left_panel.grid(row=1, column=0, sticky="nsew", padx=10, pady=10)
        ctk.CTkEntry(left_panel, placeholder_text="ابحث بالاسم أو التليفون...").pack(fill="x", padx=10, pady=5)
        self.customer_list_frame = ctk.CTkScrollableFrame(left_panel)
        self.customer_list_frame.pack(fill="both", expand=True, padx=10, pady=5)
        right_panel = ctk.CTkFrame(self)
        right_panel.grid(row=1, column=1, sticky="nsew", padx=10, pady=10)
        self.history_label = ctk.CTkLabel(right_panel, text="تاريخ طلبات العميل", font=("Arial", 18))
        self.history_label.pack(pady=10)
        self.history_textbox = ctk.CTkTextbox(right_panel, font=("Courier New", 12))
        self.history_textbox.pack(fill="both", expand=True, padx=10, pady=10)
    def load_all_customers(self):
        for widget in self.customer_list_frame.winfo_children(): widget.destroy()
        conn = sqlite3.connect('receipts.db')
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, phone FROM customers ORDER BY name")
        customers = cursor.fetchall()
        conn.close()
        for customer_id, name, phone in customers:
            btn_text = f"{name} - {phone}"
            btn = ctk.CTkButton(self.customer_list_frame, text=btn_text,
                                command=lambda c_id=customer_id, c_name=name: self.show_customer_history(c_id, c_name))
            btn.pack(fill="x", pady=2)
    def show_customer_history(self, customer_id, customer_name):
        self.history_label.configure(text=f"تاريخ طلبات العميل: {customer_name}")
        self.history_textbox.configure(state="normal")
        self.history_textbox.delete("1.0", "end")
        conn = sqlite3.connect('receipts.db')
        cursor = conn.cursor()
        cursor.execute("SELECT timestamp, total_amount, receipt_data, remaining_amount FROM receipts WHERE customer_id = ? ORDER BY timestamp DESC", (customer_id,))
        receipts = cursor.fetchall()
        conn.close()
        if not receipts:
            self.history_textbox.insert("1.0", "لا يوجد تاريخ طلبات لهذا العميل.")
        else:
            for ts, amount, data, remaining in receipts:
                date_str = datetime.strptime(ts, '%Y-%m-%d %H:%M:%S.%f').strftime('%Y-%m-%d %I:%M %p')
                remaining_str = f" | المتبقي: {remaining:.2f} جنيه" if remaining and remaining > 0 else ""
                header = f"{'='*10} فاتورة بتاريخ: {date_str} | المبلغ: {amount:.2f} جنيه{remaining_str} {'='*10}\n"
                self.history_textbox.insert("end", header, "header_tag")
                self.history_textbox.insert("end", data + "\n\n")
        self.history_textbox.tag_config("header_tag", font=("Courier New", 12, "bold"))
        self.history_textbox.configure(state="disabled")
class Page_JobTracking(ctk.CTkFrame):
    def __init__(self, parent, controller):
        super().__init__(parent)
        self.controller = controller
        ctk.CTkLabel(self, text="متابعة الطلبات الحالية", font=ctk.CTkFont(size=24, weight="bold")).pack(pady=20)
        top_frame = ctk.CTkFrame(self, fg_color="transparent")
        top_frame.pack(fill="x", padx=20, pady=5)
        ctk.CTkButton(top_frame, text="تحديث القائمة", command=self.load_open_jobs).pack(side="left")
        self.jobs_frame = ctk.CTkScrollableFrame(self)
        self.jobs_frame.pack(fill="both", expand=True, padx=20, pady=10)
    def load_open_jobs(self):
        for widget in self.jobs_frame.winfo_children(): widget.destroy()
        conn = sqlite3.connect('receipts.db')
        cursor = conn.cursor()
        cursor.execute("""
            SELECT r.id, c.name, r.due_date, r.status
            FROM receipts r JOIN customers c ON r.customer_id = c.id
            WHERE r.status != 'تم التسليم' ORDER BY r.due_date
        """)
        jobs = cursor.fetchall()
        conn.close()
        header_frame = ctk.CTkFrame(self.jobs_frame, fg_color="gray20")
        header_frame.pack(fill="x", pady=2)
        ctk.CTkLabel(header_frame, text="رقم الفاتورة", font=("Arial", 12, "bold")).pack(side="right", padx=10, expand=True)
        ctk.CTkLabel(header_frame, text="اسم العميل", font=("Arial", 12, "bold")).pack(side="right", padx=10, expand=True)
        ctk.CTkLabel(header_frame, text="تاريخ التسليم", font=("Arial", 12, "bold")).pack(side="right", padx=10, expand=True)
        ctk.CTkLabel(header_frame, text="الحالة الحالية", font=("Arial", 12, "bold")).pack(side="right", padx=10, expand=True)
        ctk.CTkLabel(header_frame, text="تغيير الحالة", font=("Arial", 12, "bold")).pack(side="left", padx=10, expand=True)
        today_str = date.today().strftime('%Y-%m-%d')
        for job_id, customer_name, due_date, status in jobs:
            job_frame = ctk.CTkFrame(self.jobs_frame)
            job_frame.pack(fill="x", pady=2)
            label_color = "tomato" if due_date and due_date <= today_str else "white"
            ctk.CTkLabel(job_frame, text=f"#{job_id}", text_color=label_color).pack(side="right", padx=10, expand=True)
            ctk.CTkLabel(job_frame, text=customer_name, text_color=label_color).pack(side="right", padx=10, expand=True)
            ctk.CTkLabel(job_frame, text=due_date, text_color=label_color).pack(side="right", padx=10, expand=True)
            ctk.CTkLabel(job_frame, text=status, text_color=label_color, font=("Arial", 12, "bold")).pack(side="right", padx=10, expand=True)
            status_var = ctk.StringVar(value=status)
            status_menu = ctk.CTkOptionMenu(job_frame, variable=status_var, values=ORDER_STATUSES,
                                            command=lambda new_status, j_id=job_id: self.update_job_status(j_id, new_status))
            status_menu.pack(side="left", padx=10, expand=True)
    def update_job_status(self, job_id, new_status):
        conn = sqlite3.connect('receipts.db')
        cursor = conn.cursor()
        if new_status == "تم التسليم":
            cursor.execute("SELECT remaining_amount FROM receipts WHERE id = ?", (job_id,))
            remaining = cursor.fetchone()[0]
            if remaining > 0:
                if messagebox.askyesno("تأكيد تسوية الدين", 
                                       f"يوجد مبلغ متبقي قدره {remaining:.2f} جنيه على هذه الفاتورة.\nهل تم استلام المبلغ بالكامل؟"):
                    cursor.execute("""
                        UPDATE receipts 
                        SET amount_paid = amount_paid + remaining_amount, 
                            remaining_amount = 0
                        WHERE id = ?
                    """, (job_id,))
                    messagebox.showinfo("نجاح", "تمت تسوية الدين بنجاح.")
        cursor.execute("UPDATE receipts SET status = ? WHERE id = ?", (new_status, job_id))
        conn.commit()
        conn.close()
        self.load_open_jobs()
        self.controller.get_frame("AdminDashboard").load_daily_summary()
class Page_DebtsTracking(ctk.CTkFrame):
    def __init__(self, parent, controller):
        super().__init__(parent)
        self.controller = controller
        ctk.CTkLabel(self, text="متابعة ديون العملاء", font=ctk.CTkFont(size=24, weight="bold")).pack(pady=20)
        top_frame = ctk.CTkFrame(self, fg_color="transparent")
        top_frame.pack(fill="x", padx=20, pady=5)
        ctk.CTkButton(top_frame, text="تحديث القائمة", command=self.load_debts).pack(side="left")
        self.debts_frame = ctk.CTkScrollableFrame(self)
        self.debts_frame.pack(fill="both", expand=True, padx=20, pady=10)
    def load_debts(self):
        for widget in self.debts_frame.winfo_children(): widget.destroy()
        conn = sqlite3.connect('receipts.db')
        cursor = conn.cursor()
        cursor.execute("""
            SELECT c.id, c.name, c.phone, SUM(r.remaining_amount), COUNT(r.id)
            FROM customers c JOIN receipts r ON c.id = r.customer_id
            WHERE r.remaining_amount > 0.01
            GROUP BY c.id
            ORDER BY SUM(r.remaining_amount) DESC
        """)
        debts = cursor.fetchall()
        conn.close()
        header_frame = ctk.CTkFrame(self.debts_frame, fg_color="gray20")
        header_frame.pack(fill="x", pady=2)
        ctk.CTkLabel(header_frame, text="اسم العميل", font=("Arial", 12, "bold")).pack(side="right", padx=10, expand=True)
        ctk.CTkLabel(header_frame, text="رقم التليفون", font=("Arial", 12, "bold")).pack(side="right", padx=10, expand=True)
        ctk.CTkLabel(header_frame, text="إجمالي المديونية", font=("Arial", 12, "bold")).pack(side="right", padx=10, expand=True)
        ctk.CTkLabel(header_frame, text="عدد الفواتير المفتوحة", font=("Arial", 12, "bold")).pack(side="right", padx=10, expand=True)
        ctk.CTkLabel(header_frame, text="إجراء", font=("Arial", 12, "bold")).pack(side="left", padx=10, expand=True)
        for customer_id, name, phone, total_remaining, count in debts:
            debt_frame = ctk.CTkFrame(self.debts_frame)
            debt_frame.pack(fill="x", pady=2)
            ctk.CTkLabel(debt_frame, text=name).pack(side="right", padx=10, expand=True)
            ctk.CTkLabel(debt_frame, text=phone).pack(side="right", padx=10, expand=True)
            ctk.CTkLabel(debt_frame, text=f"{total_remaining:.2f} جنيه", font=("Arial", 12, "bold"), text_color="tomato").pack(side="right", padx=10, expand=True)
            ctk.CTkLabel(debt_frame, text=str(count)).pack(side="right", padx=10, expand=True)
            ctk.CTkButton(debt_frame, text="عرض التفاصيل", width=100, command=lambda c_id=customer_id: self.show_customer_details(c_id)).pack(side="left", padx=10, expand=True)
    def show_customer_details(self, customer_id):
        customer_page = self.controller.get_frame("Page_CustomerManagement")
        conn = sqlite3.connect('receipts.db')
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM customers WHERE id = ?", (customer_id,))
        customer_name = cursor.fetchone()[0]
        conn.close()
        customer_page.show_customer_history(customer_id, customer_name)
        self.controller.show_frame("Page_CustomerManagement")
# <<<--- تم التعديل على هذا الكلاس بالكامل (Page_InventoryManagement) --- >>>
class Page_InventoryManagement(ctk.CTkFrame):
    def __init__(self, parent, controller):
        super().__init__(parent)
        self.controller = controller
        
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        top_frame = ctk.CTkFrame(self)
        top_frame.grid(row=0, column=0, sticky="ew", padx=20, pady=10)
        
        ctk.CTkLabel(top_frame, text="إدارة المخزون", font=ctk.CTkFont(size=24, weight="bold")).pack(side="right", padx=10)
        ctk.CTkButton(top_frame, text="إضافة مادة جديدة", command=self.add_product_popup).pack(side="left", padx=10)
        ctk.CTkButton(top_frame, text="تعديل المادة المحددة", command=self.edit_selected_product_popup).pack(side="left", padx=10)
        ctk.CTkButton(top_frame, text="تحديث القائمة", command=self.load_inventory).pack(side="left", padx=10)

        tree_frame = ctk.CTkFrame(self)
        tree_frame.grid(row=1, column=0, sticky="nsew", padx=20, pady=10)
        
        style = ttk.Style()
        style.theme_use("default")
        style.configure("Treeview", background="#2a2d2e", foreground="white", fieldbackground="#343638", borderwidth=0, font=('Arial', 12), rowheight=25)
        style.map('Treeview', background=[('selected', '#24527a')])
        style.configure("Treeview.Heading", background="#565b5e", foreground="white", relief="flat", font=('Arial', 14, 'bold'))
        style.map("Treeview.Heading", background=[('active', '#3484F0')])

        # <<<--- تعديل: إضافة سعر الشراء للأعمدة --- >>>
        self.tree = ttk.Treeview(tree_frame, columns=("price", "stock", "unit", "name", "id"), show="headings", selectmode="browse")
        self.tree.pack(side="right", fill="both", expand=True)
        
        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        vsb.pack(side='left', fill='y')
        self.tree.configure(yscrollcommand=vsb.set)

        self.tree.heading("id", text="ID")
        self.tree.heading("name", text="اسم المادة")
        self.tree.heading("unit", text="الوحدة")
        self.tree.heading("stock", text="الكمية المتاحة")
        self.tree.heading("price", text="سعر الشراء") # <<<--- تعديل

        self.tree.column("id", width=0, stretch=False) # إخفاء ID
        self.tree.column("name", width=300, anchor="e")
        self.tree.column("unit", width=100, anchor="center")
        self.tree.column("stock", width=150, anchor="center")
        self.tree.column("price", width=150, anchor="center") # <<<--- تعديل
        
        self.tree['displaycolumns'] = ("price", "stock", "unit", "name")
        self.tree.bind("<Double-1>", self.on_item_double_click)

    def load_inventory(self):
        for item in self.tree.get_children():
            self.tree.delete(item)

        conn = sqlite3.connect('receipts.db')
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, unit, stock_level, low_stock_threshold, purchase_price FROM inventory ORDER BY name")
        items = cursor.fetchall()
        conn.close()

        for item in items:
            item_id, name, unit, stock, threshold, price = item
            tags = ()
            if stock <= threshold:
                tags = ('low_stock',)
            
            price_str = f"{price:.2f} ج.م"
            # <<<--- تعديل: إضافة سعر الشراء للقيم --- >>>
            self.tree.insert("", "end", values=(price_str, stock, unit, name, item_id), tags=tags)
        
        self.tree.tag_configure('low_stock', background='#E74C3C', foreground='white')

    def on_item_double_click(self, event):
        if not self.tree.selection(): return
        item_id = self.tree.selection()[0]
        selected_item = self.tree.item(item_id)
        price_str, stock, unit, name, db_id = selected_item['values']
        self.adjust_stock_popup(db_id, name, stock)

    def add_product_popup(self):
        self.product_form_popup(is_edit=False)

    def edit_selected_product_popup(self):
        if not self.tree.selection():
            messagebox.showwarning("تنبيه", "الرجاء تحديد مادة من القائمة لتعديلها.")
            return
        
        item_id = self.tree.selection()[0]
        selected_item = self.tree.item(item_id)
        price_str, stock, unit, name, db_id = selected_item['values']
        
        conn = sqlite3.connect('receipts.db')
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM inventory WHERE id = ?", (db_id,))
        product_data = cursor.fetchone()
        conn.close()
        
        self.product_form_popup(is_edit=True, data=product_data)
        
    def product_form_popup(self, is_edit, data=None):
        popup = ctk.CTkToplevel(self)
        title = "تعديل بيانات المادة" if is_edit else "إضافة مادة خام جديدة"
        popup.title(title)
        popup.geometry("400x450")
        popup.transient(self)
        popup.grab_set()
        
        ctk.CTkLabel(popup, text="اسم المادة:", font=("Arial", 14)).pack(pady=(15, 5))
        name_entry = ctk.CTkEntry(popup, width=300)
        name_entry.pack()
        
        ctk.CTkLabel(popup, text="وحدة القياس:", font=("Arial", 14)).pack(pady=(10, 5))
        unit_entry = ctk.CTkEntry(popup, width=300)
        unit_entry.pack()
        
        # <<<--- تعديل: إضافة حقل سعر الشراء --- >>>
        ctk.CTkLabel(popup, text="سعر الشراء للوحدة:", font=("Arial", 14)).pack(pady=(10, 5))
        price_entry = ctk.CTkEntry(popup, width=300)
        price_entry.pack()

        if not is_edit:
            ctk.CTkLabel(popup, text="الكمية المبدئية:", font=("Arial", 14)).pack(pady=(10, 5))
            stock_entry = ctk.CTkEntry(popup, width=300)
            stock_entry.pack()
        
        ctk.CTkLabel(popup, text="حد التنبيه (عندما يقل المخزون عن هذا الرقم):", font=("Arial", 14)).pack(pady=(10, 5))
        threshold_entry = ctk.CTkEntry(popup, width=300)
        threshold_entry.pack()

        if is_edit:
            # data: id, name, unit, stock_level, low_stock_threshold, purchase_price
            name_entry.insert(0, data[1])
            unit_entry.insert(0, data[2])
            threshold_entry.insert(0, str(data[4]))
            price_entry.insert(0, str(data[5]))

        def save_product():
            name = name_entry.get().strip()
            unit = unit_entry.get().strip()
            price_str = price_entry.get()
            threshold_str = threshold_entry.get()
            stock_str = "0" if is_edit else stock_entry.get()

            if not all([name, unit, price_str, threshold_str, stock_str]):
                messagebox.showerror("خطأ", "يجب ملء جميع الحقول.", parent=popup)
                return
            
            try:
                stock = float(stock_str)
                threshold = float(threshold_str)
                price = float(price_str)
            except (ValueError, TypeError):
                messagebox.showerror("خطأ", "الكمية والسعر وحد التنبيه يجب أن تكون أرقاماً.", parent=popup)
                return
            
            try:
                conn = sqlite3.connect('receipts.db')
                cursor = conn.cursor()
                if is_edit:
                    cursor.execute("""
                        UPDATE inventory SET name=?, unit=?, low_stock_threshold=?, purchase_price=? 
                        WHERE id = ?
                    """, (name, unit, threshold, price, data[0]))
                else:
                    cursor.execute("""
                        INSERT INTO inventory (name, unit, stock_level, low_stock_threshold, purchase_price) 
                        VALUES (?, ?, ?, ?, ?)
                    """, (name, unit, stock, threshold, price))
                conn.commit()
                conn.close()
                messagebox.showinfo("نجاح", f"تم حفظ بيانات المادة بنجاح.", parent=popup)
                self.load_inventory()
                popup.destroy()
            except sqlite3.IntegrityError:
                messagebox.showerror("خطأ", "هذا الاسم موجود بالفعل في المخزون.", parent=popup)
            except Exception as e:
                messagebox.showerror("خطأ", f"حدث خطأ غير متوقع: {e}", parent=popup)

        ctk.CTkButton(popup, text="حفظ", command=save_product).pack(pady=20)

    def adjust_stock_popup(self, item_id, item_name, current_stock):
        popup = ctk.CTkToplevel(self)
        popup.title(f"إضافة مخزون لـ: {item_name}")
        popup.geometry("400x250")
        popup.transient(self)
        popup.grab_set()

        ctk.CTkLabel(popup, text=f"الكمية الحالية: {current_stock}", font=("Arial", 16)).pack(pady=15)
        ctk.CTkLabel(popup, text="أدخل الكمية *للإضافة* إلى المخزون:", font=("Arial", 14)).pack(pady=(10, 5))
        
        add_entry = ctk.CTkEntry(popup, width=300, placeholder_text="0.0")
        add_entry.pack()

        def update_stock():
            add_str = add_entry.get()
            if not add_str:
                popup.destroy()
                return

            try:
                quantity_to_add = float(add_str)
            except (ValueError, TypeError):
                messagebox.showerror("خطأ", "الرجاء إدخال رقم صحيح.", parent=popup)
                return

            try:
                conn = sqlite3.connect('receipts.db')
                cursor = conn.cursor()
                cursor.execute("UPDATE inventory SET stock_level = stock_level + ? WHERE id = ?", (quantity_to_add, item_id))
                conn.commit()
                conn.close()
                messagebox.showinfo("نجاح", "تم تحديث كمية المخزون.", parent=popup)
                self.load_inventory()
                popup.destroy()
            except Exception as e:
                messagebox.showerror("خطأ", f"حدث خطأ أثناء التحديث: {e}", parent=popup)

        ctk.CTkButton(popup, text="تحديث الكمية", command=update_stock).pack(pady=20)


# ... (كلاس Page_PriceManagement يبقى كما هو) ...
class Page_PriceManagement(ctk.CTkFrame):
    def __init__(self, parent, controller):
        super().__init__(parent)
        self.controller = controller
        self.price_entries = {}
        self.prices_data = {}

        ctk.CTkLabel(self, text="إدارة وتعديل الأسعار", font=ctk.CTkFont(size=24, weight="bold")).pack(pady=20)
        
        self.scrollable_frame = ctk.CTkScrollableFrame(self)
        self.scrollable_frame.pack(fill="both", expand=True, padx=20, pady=10)

        bottom_frame = ctk.CTkFrame(self, fg_color="transparent")
        bottom_frame.pack(pady=10, fill="x", padx=20)
        
        ctk.CTkButton(bottom_frame, text="حفظ التعديلات", font=("Arial", 16, "bold"), height=40,
                      fg_color="#27ae60", hover_color="#2ecc71", command=self.save_changes).pack(side="left", padx=10)
        ctk.CTkButton(bottom_frame, text="إعادة تعيين للأسعار الافتراضية", font=("Arial", 14),
                      fg_color="#e67e22", hover_color="#d35400", command=self.reset_to_defaults).pack(side="left", padx=10)
        
        # self.populate_prices() # Will be called by show_frame

    def populate_prices(self):
        for widget in self.scrollable_frame.winfo_children():
            widget.destroy()

        self.prices_data = config_manager.load_prices()
        self.price_entries = {}
        row_counter = 0

        def create_entry(parent, text, value, path):
            nonlocal row_counter
            frame = ctk.CTkFrame(parent, fg_color="transparent")
            frame.grid(row=row_counter, column=0, sticky="ew", pady=2)
            ctk.CTkLabel(frame, text=text, anchor="w").pack(side="right", padx=10, fill="x", expand=True)
            entry = ctk.CTkEntry(frame, width=100)
            entry.insert(0, str(value))
            entry.pack(side="left", padx=10)
            self.price_entries[path] = entry
            row_counter += 1

        def create_header(text):
            nonlocal row_counter
            ctk.CTkLabel(self.scrollable_frame, text=text, font=ctk.CTkFont(size=16, weight="bold"), anchor="e").grid(row=row_counter, column=0, sticky="ew", pady=(10, 5), padx=10)
            row_counter += 1

        # -- Kocheh Prices --
        create_header("أسعار طباعة الكوشيه والاستيكر")
        for p_type, sides in self.prices_data['PRINTING_PRICES'].items():
            for side, price in sides.items():
                create_entry(self.scrollable_frame, f"{p_type} ({side})", price, ('PRINTING_PRICES', p_type, side))

        # -- Finishing Prices --
        create_header("أسعار التشطيبات (سلوفان، تشريح، تجليد)")
        for key, price in self.prices_data['LAMINATION_PRICES'].items():
            if key != "لا يوجد":
                create_entry(self.scrollable_frame, f"سلوفان: {key}", price, ('LAMINATION_PRICES', key))
        for key, price in self.prices_data['TRIMMING_PRICES'].items():
            if key != "لا يوجد":
                create_entry(self.scrollable_frame, f"تشريح: {key}", price, ('TRIMMING_PRICES', key))
        create_entry(self.scrollable_frame, "سعر القص الأدنى", self.prices_data['MIN_CUTTING_PRICE'], ('MIN_CUTTING_PRICE',))

        # -- Plain Paper Prices --
        create_header("أسعار طباعة الورق العادي (Ink)")
        for p_type, sizes in self.prices_data['PLAIN_PAPER_PRICES'].items():
            for size, brackets in sizes.items():
                for bracket, sides in brackets.items():
                    qty_text = "كميات كبيرة" if bracket == "large" else "كميات صغيرة"
                    for side, price in sides.items():
                         create_entry(self.scrollable_frame, f"{p_type} {size} ({qty_text}) - {side}", price, ('PLAIN_PAPER_PRICES', p_type, size, bracket, side))
        
        # -- Laser Paper Prices --
        create_header("أسعار طباعة الورق العادي (ليزر)")
        for p_type, sizes in self.prices_data['LASER_PLAIN_PAPER_PRICES'].items():
            for size, sides in sizes.items():
                for side, price in sides.items():
                     create_entry(self.scrollable_frame, f"{p_type} {size} - {side}", price, ('LASER_PLAIN_PAPER_PRICES', p_type, size, side))

        # <<<--- الإضافة الجديدة لأسعار كروت ID --- >>>
        create_header("أسعار كروت ID (حسب الكمية)")
        for i, tier in enumerate(self.prices_data['ID_CARD_PRICING']):
            limit, price = tier
            
            display_limit = 999999 if limit == float('inf') else limit

            tier_frame = ctk.CTkFrame(self.scrollable_frame)
            tier_frame.grid(row=row_counter, column=0, sticky="ew", pady=3, padx=5)

            ctk.CTkLabel(tier_frame, text=f"الشريحة رقم {i+1}", font=ctk.CTkFont(weight="bold")).pack(side="right", padx=10)
            
            price_entry = ctk.CTkEntry(tier_frame, width=80)
            price_entry.insert(0, str(price))
            price_entry.pack(side="left", padx=5)
            ctk.CTkLabel(tier_frame, text="السعر:").pack(side="left", padx=(10, 0))
            self.price_entries[('ID_CARD_PRICING', i, 1)] = price_entry

            limit_entry = ctk.CTkEntry(tier_frame, width=80)
            limit_entry.insert(0, str(display_limit))
            limit_entry.pack(side="left", padx=5)
            ctk.CTkLabel(tier_frame, text="حتى كمية:").pack(side="left", padx=(10, 0))
            self.price_entries[('ID_CARD_PRICING', i, 0)] = limit_entry
            
            row_counter += 1

    def save_changes(self):
        try:
            for path, entry in self.price_entries.items():
                new_value = float(entry.get())
                
                temp = self.prices_data
                for key in path[:-1]:
                    temp = temp[key]
                temp[path[-1]] = new_value

            config_manager.save_prices(self.prices_data)
            messagebox.showinfo("نجاح", "تم حفظ الأسعار بنجاح!\nيرجى إعادة تشغيل البرنامج لتطبيق التغييرات.")
        except ValueError:
            messagebox.showerror("خطأ", "الرجاء إدخال أرقام صالحة فقط في حقول الأسعار.")
        except Exception as e:
            messagebox.showerror("خطأ", f"حدث خطأ أثناء الحفظ: {e}")

    def reset_to_defaults(self):
        if messagebox.askyesno("تأكيد", "هل أنت متأكد من رغبتك في إعادة كل الأسعار إلى الوضع الافتراضي؟"):
            default_prices = config_manager.get_default_prices()
            config_manager.save_prices(default_prices)
            self.populate_prices()
            messagebox.showinfo("نجاح", "تمت استعادة الأسعار الافتراضية.\nيرجى إعادة تشغيل البرنامج.")
# ==============================================================================
# 7. تشغيل التطبيق
# ==============================================================================
if __name__ == "__main__":
    init_database()
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("dark-blue")
    
    app = CashierApp()
    app.mainloop()
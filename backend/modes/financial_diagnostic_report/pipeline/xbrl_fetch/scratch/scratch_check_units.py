import glob
import os
import xml.etree.ElementTree as ET

matches = glob.glob(r'D:\finance_llm_integrated_v1\XBRL\XBRL\INDAS\INDAS\42_N31216054_20250611_ZA4X_U74995TG2018SGC124*.xml')
if matches:
    fpath = matches[0]
    print('Checking file:', os.path.basename(fpath))
    tree = ET.parse(fpath)
    root = tree.getroot()
    for elem in root.iter():
        tag = elem.tag.split('}')[-1]
        if any(t in tag for t in ['NameOfCompany', 'CorporateIdentityNumber', 'PaidUp', 'Equity', 'Assets', 'RevenueFromOperations', 'ProfitLossForPeriod']):
            txt = (elem.text or '').strip()
            if len(txt) < 80 and txt:
                unit = elem.attrib.get('unitRef')
                dec = elem.attrib.get('decimals')
                print(f'{tag:35}: {txt} | unit={unit} | dec={dec}')


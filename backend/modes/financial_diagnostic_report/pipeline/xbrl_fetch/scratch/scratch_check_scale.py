import glob
import os
import xml.etree.ElementTree as ET

xml_files = glob.glob(r'D:\finance_llm_integrated_v1\XBRL\XBRL\INDAS\INDAS\*.xml')

anomalies = []

for idx, fpath in enumerate(xml_files):
    fname = os.path.basename(fpath)
    try:
        tree = ET.parse(fpath)
        root = tree.getroot()
        shares = None
        share_cap = None
        for elem in root.iter():
            tag = elem.tag.split('}')[-1]
            if tag in ['NumberOfSharesPaidUp', 'NumberOfEquitySharesSubscribed']:
                txt = (elem.text or '').strip()
                if txt and txt.replace('.', '').isdigit():
                    v = float(txt)
                    if v > 0 and (shares is None or v > shares):
                        shares = v
            if tag in ['PaidUpEquityShareCapital', 'EquityShareCapital'] and elem.attrib.get('unitRef') == 'INR':
                txt = (elem.text or '').strip()
                if txt:
                    try:
                        v = float(txt)
                        if v > 0 and (share_cap is None or v > share_cap):
                            share_cap = v
                    except ValueError:
                        pass
        
        # In India, typical face value per share is 1, 2, 5, or 10 rupees.
        # If share_cap / shares is approx 1, 2, 5, 10, then share_cap is in ABSOLUTE RUPEES.
        # If share_cap / shares is 0.0001, then share_cap was entered in Lakhs or Crores!
        if shares and share_cap and shares > 1000:
            face_value = share_cap / shares
            if face_value < 0.1:  # suspiciously small face value!
                anomalies.append((fname[:45], shares, share_cap, face_value))
    except Exception:
        pass

print(f"Scanned {len(xml_files)} files. Found {len(anomalies)} filings with apparent scale/unit mismatch:")
for a in anomalies:
    print(a)


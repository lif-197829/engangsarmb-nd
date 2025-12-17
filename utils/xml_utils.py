# utils/xml_utils.py
import xml.etree.ElementTree as ET

def _localname(tag) -> str:
    t = str(tag)
    return t.split('}', 1)[-1] if '}' in t else t

def sort_children_alphabetically(elem: ET.Element):
    elem[:] = sorted(list(elem), key=lambda e: _localname(e.tag))
    for child in elem:
        sort_children_alphabetically(child)

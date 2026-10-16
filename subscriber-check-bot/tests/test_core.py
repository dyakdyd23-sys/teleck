from app.phones import normalize,parse_file,display
from app.export import HEAD

def test_numbers():
    assert normalize('010-1234-5678')=='01012345678'
    assert normalize('+821012345678')=='01012345678'
    assert normalize('0101234') is None
    assert normalize('not a number') is None
    assert display('01012345678')=='010-1234-5678'

def test_csv():
    result=parse_file('test.csv','전화번호\n01012345678\n010-1234-5678\n+821098765432\nwrong'.encode())
    assert (result['total'],result['valid'],result['duplicates'],result['invalid'])==(4,2,1,1)

def test_headers():
    assert '텔레그램 UID' in HEAD

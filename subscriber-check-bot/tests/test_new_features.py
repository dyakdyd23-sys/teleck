import io
from app.phones import normalize, parse_file, display
from openpyxl import Workbook

def test_korean_country_code_and_local_normalization():
    for val in ('8210-1234-5678','+821012345678','82 10 1234 5678','010-1234-5678','01012345678'):
        assert normalize(val)=='01012345678'
        assert display(normalize(val))=='010-1234-5678'
    assert normalize('8210-1234-567') is None
    assert normalize('8200-1234-5678') is None

def test_txt_upload_deduplication():
    result=parse_file('phones.txt','전화번호\n8210-1234-5678\n010-1234-5678\n01099998888\ninvalid\n'.encode())
    assert result['valid']==2 and result['duplicates']==1 and result['invalid']==1

def test_excel_upload():
    wb=Workbook();ws=wb.active;ws.append(['전화번호']);ws.append(['8210-1234-5678']);out=io.BytesIO();wb.save(out)
    assert parse_file('phones.xlsx',out.getvalue())['phones']==['01012345678']

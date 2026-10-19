from decimal import Decimal, InvalidOperation

def parse_krw_tenths(value):
    try:
        amount=Decimal(str(value).strip())
        if not amount.is_finite() or amount<=0 or amount>Decimal('1000000000') or amount*10!=(amount*10).to_integral_value():
            raise ValueError('충전금액은 0.1원 단위의 양수로 입력해 주세요.')
        return int(amount*10)
    except (InvalidOperation,TypeError):
        raise ValueError('충전금액 입력 형식이 올바르지 않습니다.')


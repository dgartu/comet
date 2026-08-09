_URI_SAFE_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+-$"
_URI_SAFE_REVERSE = {
    character: index for index, character in enumerate(_URI_SAFE_ALPHABET)
}


def decompressFromEncodedURIComponent(input_str):
    if input_str is None:
        return ""
    if input_str == "":
        return None
    if type(input_str) is not str:
        return None

    input_str = input_str.replace(" ", "+")

    input_data = [_URI_SAFE_REVERSE.get(character) for character in input_str]
    if any(value is None for value in input_data):
        return None

    return _decompress(len(input_data), 32, input_data)


def _decompress(length, reset_value, input_data):
    dictionary = [0, 1, 2]
    enlarge_in = 4
    dictionary_size = 4
    bit_count = 3
    result = []

    data_value = input_data[0]
    position = reset_value
    index = 1

    def read_bits(count):
        nonlocal data_value, index, position
        bits = 0
        power = 1
        maximum_power = 1 << count
        while power != maximum_power:
            bit = data_value & position
            position >>= 1
            if position == 0:
                position = reset_value
                if index >= length:
                    return None
                data_value = input_data[index]
                index += 1
            if bit:
                bits |= power
            power <<= 1
        return bits

    next_value = read_bits(2)
    if next_value == 0:
        bits = read_bits(8)
        if bits is None:
            return None
        character = chr(bits)
    elif next_value == 1:
        bits = read_bits(16)
        if bits is None:
            return None
        character = chr(bits)
    elif next_value == 2:
        return ""
    else:
        return None

    dictionary.append(character)
    previous = character
    result.append(character)

    while True:
        code = read_bits(bit_count)
        if code is None:
            return None
        if code == 0:
            bits = read_bits(8)
            if bits is None:
                return None
            dictionary.append(chr(bits))
            code = dictionary_size
            dictionary_size += 1
            enlarge_in -= 1
        elif code == 1:
            bits = read_bits(16)
            if bits is None:
                return None
            dictionary.append(chr(bits))
            code = dictionary_size
            dictionary_size += 1
            enlarge_in -= 1
        elif code == 2:
            return "".join(result)

        if enlarge_in == 0:
            enlarge_in = 1 << bit_count
            bit_count += 1

        if code < len(dictionary):
            entry = dictionary[code]
        elif code == dictionary_size:
            entry = previous + previous[0]
        else:
            return None

        result.append(entry)

        dictionary.append(previous + entry[0])
        dictionary_size += 1
        enlarge_in -= 1

        previous = entry

        if enlarge_in == 0:
            enlarge_in = 1 << bit_count
            bit_count += 1

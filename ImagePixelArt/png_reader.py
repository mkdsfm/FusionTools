from dataclasses import dataclass
import struct
import zlib


PNG_SIGNATURE = b'\x89PNG\r\n\x1a\n'
MAX_SOURCE_PIXELS = 50_000_000
ADAM7_PASSES = (
    (0, 0, 8, 8),
    (4, 0, 8, 8),
    (0, 4, 4, 8),
    (2, 0, 4, 4),
    (0, 2, 2, 4),
    (1, 0, 2, 2),
    (0, 1, 1, 2)
)


@dataclass(frozen=True)
class RgbImage:
    width: int
    height: int
    pixels: tuple[tuple[int, int, int], ...]


def read_png(path: str) -> RgbImage:
    with open(path, 'rb') as stream:
        data = stream.read()

    if not data.startswith(PNG_SIGNATURE):
        raise ValueError('The selected file is not a valid PNG image.')

    chunks = _read_chunks(data[len(PNG_SIGNATURE):])
    ihdr_chunks = [chunk for chunk_type, chunk in chunks if chunk_type == b'IHDR']
    if len(ihdr_chunks) != 1 or len(ihdr_chunks[0]) != 13:
        raise ValueError('The PNG has an invalid IHDR chunk.')

    width, height, bit_depth, color_type, compression, filter_method, interlace = struct.unpack(
        '>IIBBBBB', ihdr_chunks[0]
    )
    if width < 1 or height < 1 or width * height > MAX_SOURCE_PIXELS:
        raise ValueError(f'PNG dimensions are invalid or exceed {MAX_SOURCE_PIXELS:,} pixels.')
    if compression != 0 or filter_method != 0 or interlace not in (0, 1):
        raise ValueError('The PNG uses an unsupported compression, filter, or interlace method.')

    channels = _validate_color_format(color_type, bit_depth)
    palette_data = next((chunk for chunk_type, chunk in chunks if chunk_type == b'PLTE'), None)
    transparency = next((chunk for chunk_type, chunk in chunks if chunk_type == b'tRNS'), None)
    palette = _read_palette(palette_data, transparency) if color_type == 3 else None
    compressed = b''.join(chunk for chunk_type, chunk in chunks if chunk_type == b'IDAT')
    if not compressed:
        raise ValueError('The PNG contains no image data.')

    inflated = _decompress_limited(compressed, width, height, channels, bit_depth)
    rgba_pixels = _decode_image(inflated, width, height, channels, bit_depth, color_type, interlace, palette, transparency)
    rgb_pixels = tuple(_composite_over_white(red, green, blue, alpha) for red, green, blue, alpha in rgba_pixels)
    return RgbImage(width, height, rgb_pixels)


def _read_chunks(data: bytes) -> list[tuple[bytes, bytes]]:
    chunks = []
    offset = 0
    saw_iend = False

    while offset + 12 <= len(data):
        length = struct.unpack_from('>I', data, offset)[0]
        chunk_end = offset + 12 + length
        if chunk_end > len(data):
            raise ValueError('The PNG contains a truncated chunk.')

        chunk_type = data[offset + 4:offset + 8]
        chunk_data = data[offset + 8:offset + 8 + length]
        expected_crc = struct.unpack_from('>I', data, offset + 8 + length)[0]
        actual_crc = zlib.crc32(chunk_type + chunk_data) & 0xFFFFFFFF
        if actual_crc != expected_crc:
            raise ValueError(f'The PNG chunk {chunk_type.decode("ascii", "replace")} has an invalid checksum.')

        chunks.append((chunk_type, chunk_data))
        offset = chunk_end
        if chunk_type == b'IEND':
            saw_iend = True
            break

    if not saw_iend:
        raise ValueError('The PNG contains no IEND chunk.')
    return chunks


def _validate_color_format(color_type: int, bit_depth: int) -> int:
    valid_depths = {
        0: (1, 2, 4, 8, 16),
        2: (8, 16),
        3: (1, 2, 4, 8),
        4: (8, 16),
        6: (8, 16)
    }
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
    if color_type not in valid_depths or bit_depth not in valid_depths[color_type]:
        raise ValueError(f'Unsupported PNG color type {color_type} with {bit_depth}-bit samples.')
    return channels[color_type]


def _read_palette(data: bytes | None, transparency: bytes | None) -> tuple[tuple[int, int, int, int], ...]:
    if not data or len(data) % 3 != 0 or len(data) > 768:
        raise ValueError('The indexed PNG has an invalid or missing palette.')

    entries = []
    for index in range(len(data) // 3):
        red, green, blue = data[index * 3:index * 3 + 3]
        alpha = transparency[index] if transparency and index < len(transparency) else 255
        entries.append((red, green, blue, alpha))
    return tuple(entries)


def _decompress_limited(compressed: bytes, width: int, height: int, channels: int, bit_depth: int) -> bytes:
    limit = ((width * channels * bit_depth + 7) // 8) * height + height * 8 + 1024
    decompressor = zlib.decompressobj()
    inflated = decompressor.decompress(compressed, limit + 1)
    if len(inflated) > limit or decompressor.unconsumed_tail:
        raise ValueError('The PNG expands beyond its expected image-data size.')
    inflated += decompressor.flush(limit + 1 - len(inflated))
    if len(inflated) > limit or not decompressor.eof:
        raise ValueError('The PNG image data is invalid or truncated.')
    return inflated


def _decode_image(data, width, height, channels, bit_depth, color_type, interlace, palette, transparency):
    pixels = [(0, 0, 0, 0)] * (width * height)
    passes = ((0, 0, 1, 1),) if interlace == 0 else ADAM7_PASSES
    offset = 0

    for start_x, start_y, step_x, step_y in passes:
        pass_width = _pass_size(width, start_x, step_x)
        pass_height = _pass_size(height, start_y, step_y)
        if pass_width == 0 or pass_height == 0:
            continue

        row_bytes = (pass_width * channels * bit_depth + 7) // 8
        bytes_per_pixel = max(1, (channels * bit_depth + 7) // 8)
        previous = bytearray(row_bytes)

        for pass_y in range(pass_height):
            if offset + 1 + row_bytes > len(data):
                raise ValueError('The PNG image data is truncated.')
            filter_type = data[offset]
            encoded = data[offset + 1:offset + 1 + row_bytes]
            offset += 1 + row_bytes
            row = _unfilter(encoded, previous, bytes_per_pixel, filter_type)
            row_pixels = _decode_row(row, pass_width, channels, bit_depth, color_type, palette, transparency)
            target_y = start_y + pass_y * step_y

            for pass_x, pixel in enumerate(row_pixels):
                target_x = start_x + pass_x * step_x
                pixels[target_y * width + target_x] = pixel
            previous = row

    if offset != len(data):
        raise ValueError('The PNG contains unexpected trailing image data.')
    return pixels


def _pass_size(full_size: int, start: int, step: int) -> int:
    return 0 if full_size <= start else (full_size - start + step - 1) // step


def _unfilter(encoded: bytes, previous: bytearray, bpp: int, filter_type: int) -> bytearray:
    if filter_type not in range(5):
        raise ValueError(f'The PNG uses unknown row filter {filter_type}.')

    result = bytearray(len(encoded))
    for index, value in enumerate(encoded):
        left = result[index - bpp] if index >= bpp else 0
        up = previous[index]
        upper_left = previous[index - bpp] if index >= bpp else 0

        if filter_type == 0:
            predictor = 0
        elif filter_type == 1:
            predictor = left
        elif filter_type == 2:
            predictor = up
        elif filter_type == 3:
            predictor = (left + up) // 2
        else:
            predictor = _paeth(left, up, upper_left)
        result[index] = (value + predictor) & 0xFF
    return result


def _paeth(left: int, up: int, upper_left: int) -> int:
    estimate = left + up - upper_left
    left_distance = abs(estimate - left)
    up_distance = abs(estimate - up)
    upper_left_distance = abs(estimate - upper_left)
    if left_distance <= up_distance and left_distance <= upper_left_distance:
        return left
    return up if up_distance <= upper_left_distance else upper_left


def _decode_row(row, width, channels, bit_depth, color_type, palette, transparency):
    samples = _unpack_samples(row, width * channels, bit_depth)
    max_sample = (1 << bit_depth) - 1
    transparent_gray = struct.unpack('>H', transparency)[0] if color_type == 0 and transparency and len(transparency) >= 2 else None
    transparent_rgb = struct.unpack('>HHH', transparency[:6]) if color_type == 2 and transparency and len(transparency) >= 6 else None
    result = []

    for index in range(width):
        values = samples[index * channels:(index + 1) * channels]
        if color_type == 0:
            gray = _scale_sample(values[0], max_sample)
            alpha = 0 if values[0] == transparent_gray else 255
            result.append((gray, gray, gray, alpha))
        elif color_type == 2:
            red, green, blue = (_scale_sample(value, max_sample) for value in values)
            alpha = 0 if tuple(values) == transparent_rgb else 255
            result.append((red, green, blue, alpha))
        elif color_type == 3:
            palette_index = values[0]
            if palette_index >= len(palette):
                raise ValueError('The PNG references a palette entry that does not exist.')
            result.append(palette[palette_index])
        elif color_type == 4:
            gray = _scale_sample(values[0], max_sample)
            result.append((gray, gray, gray, _scale_sample(values[1], max_sample)))
        else:
            result.append(tuple(_scale_sample(value, max_sample) for value in values))
    return result


def _unpack_samples(row: bytes, sample_count: int, bit_depth: int) -> list[int]:
    if bit_depth == 8:
        return list(row[:sample_count])
    if bit_depth == 16:
        return [struct.unpack_from('>H', row, index * 2)[0] for index in range(sample_count)]

    mask = (1 << bit_depth) - 1
    samples = []
    for sample_index in range(sample_count):
        bit_offset = sample_index * bit_depth
        byte_value = row[bit_offset // 8]
        shift = 8 - bit_depth - (bit_offset % 8)
        samples.append((byte_value >> shift) & mask)
    return samples


def _scale_sample(value: int, max_sample: int) -> int:
    return (value * 255 + max_sample // 2) // max_sample


def _composite_over_white(red: int, green: int, blue: int, alpha: int) -> tuple[int, int, int]:
    inverse = 255 - alpha
    return (
        (red * alpha + 255 * inverse + 127) // 255,
        (green * alpha + 255 * inverse + 127) // 255,
        (blue * alpha + 255 * inverse + 127) // 255
    )

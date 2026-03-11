"""
Decodes .bepis files (save data from UTRAKILL, c# object stream format)
Displays the position of most properties, for easy editing in a hex editor*
* Note: .bepis files are little endian!
Package requirements: rich
Usage: decode_bepis.py to_decode.bepis
"""

import os
import struct
import sys
from pathlib import Path
from typing import IO

from rich.console import Console

console = Console()

indent = 0

type_names = [
    "bool",
    "byte",
    "char",
    "???",
    "decimal",
    "double",
    "i16",
    "i32",
    "i64",
    "i8",
    "single",
    "time_span",
    "date_time",
    "u16",
    "u32",
    "u64",
    "null",
    "string",
]

prim_readers = [
    lambda x: bool(x.read(1)[0]),  # 1
    lambda x: x.read(1)[0],  # 2
    lambda x: chr(x.read(1)[0]),  # 3
    None,  # 4
    lambda x: _read_str(x),  # 5
    lambda x: struct.unpack("<d", x.read(8))[0],  # 6
    lambda x: struct.unpack("<h", x.read(2))[0],  # 7
    lambda x: struct.unpack("<i", x.read(4))[0],  # 8
    lambda x: struct.unpack("<l", x.read(8))[0],  # 9
    lambda x: struct.unpack("<b", x.read(1))[0],  # 10
    lambda x: struct.unpack("<f", x.read(4))[0],  # 11
    lambda x: struct.unpack("<l", x.read(8))[0],  # 12
    lambda x: (x.read(8), 0)[1],  # 13, i cannot be fucked to parse this
    lambda x: struct.unpack("<H", x.read(2))[0],  # 14
    lambda x: struct.unpack("<I", x.read(4))[0],  # 15
    lambda x: struct.unpack("<L", x.read(8))[0],  # 16
    lambda x: None,  # 17
    lambda x: _read_str(x),  # 18
]

bin_type_enums = [
    "Primitive  ",
    "String     ",
    "Object     ",
    "SystemClass",
    "Class      ",
    "Object[]   ",
    "String[]   ",
    "Primitive[]",
]


def _pos_of(i: IO, offset: int = 0):
    return hex(i.tell() + offset)[:]


def _read_str(i: IO):
    ln = i.read(1)[0]
    return i.read(ln).decode("utf8")


def fancy_print(t):
    console.print("  " * indent + t)


def print_prop(a, b):
    fancy_print(f"[green]{a}[/green] = [blue]{b}[/blue]")


def ind():
    global indent
    indent += 1


def ded():
    global indent
    indent -= 1


class indented:
    def __init__(self, name):
        self.name = name

    def __enter__(self):
        global indent
        fancy_print(self.name + " {")
        indent += 1

    def __exit__(self, exc_type, exc_val, exc_tb):
        global indent
        indent -= 1
        fancy_print("}")


def _decode_ser_header_record(i: IO):
    start = _pos_of(i, -1)
    root_id = struct.unpack("<i", i.read(4))[0]
    header_id = struct.unpack("<i", i.read(4))[0]
    maj_ver = struct.unpack("<i", i.read(4))[0]
    min_ver = struct.unpack("<i", i.read(4))[0]
    end = _pos_of(i, -1)
    with indented(f"SerializedStreamHeader [yellow]({start}, {end})[/yellow]"):
        print_prop("root_id", root_id)
        print_prop("header_id", header_id)
        print_prop("major_ver", maj_ver)
        print_prop("minor_ver", min_ver)


def _decode_class_with_id(i: IO):
    start = _pos_of(i, -1)
    obj_id = struct.unpack("<i", i.read(4))[0]
    metadata_id = struct.unpack("<i", i.read(4))[0]
    end = _pos_of(i, -1)
    with indented(f"ClassWithId [yellow]({start}, {end})[/yellow]"):
        print_prop("object_id", obj_id)
        print_prop("metadata_id", metadata_id)


class ClassInfo:
    def __init__(self, i: IO):
        self.io = i
        self.object_id = 0
        self.name = ""
        self.members = []

    def populate(self):
        self.object_id = struct.unpack("<i", self.io.read(4))[0]
        self.name = _read_str(self.io)
        m_cnt = struct.unpack("<i", self.io.read(4))[0]
        for _ in range(m_cnt):
            self.members.append(_read_str(self.io))


class MemberTypeInfo:
    def __init__(self, i: IO, member_count: int):
        self.io = i
        self.member_count = member_count
        self.bin_type_enums = []
        self.additional_infos = []

    def populate(self):
        r = self.io.read(self.member_count)  # just one byte per enum element
        for x in r:
            if x == 0 or x == 7:
                self.additional_infos += self.io.read(1)
                # self.additional_infos.append(
                #     "Type: " + type_names[self.io.read(1)[0] - 1]
                # )  # type of primitive
            elif x == 3:
                self.additional_infos.append(_read_str(self.io))
            elif x == 4:
                type_name = _read_str(self.io)
                lib_id = struct.unpack("<i", self.io.read(4))[0]
                self.additional_infos.append((type_name, lib_id))
            else:
                self.additional_infos.append(None)
        self.bin_type_enums = list(r)


def _decode_cls_with_members_and_types(i: IO):
    start = _pos_of(i, -1)
    start_ci = _pos_of(i)
    cls_info = ClassInfo(i)
    cls_info.populate()
    end_ci = _pos_of(i, -1)
    start_mti = _pos_of(i)
    member_type_info = MemberTypeInfo(i, len(cls_info.members))
    member_type_info.populate()
    end_mti = _pos_of(i, -1)
    lib_id = struct.unpack("<i", i.read(4))[0]
    values = []

    for (x, info) in zip(
        member_type_info.bin_type_enums, member_type_info.additional_infos
    ):
        if x == 0:  # primitive, nothing special
            if info == 18:  # string
                v = i.read(1)[0]
                if v != 6:
                    raise ValueError(
                        "Got object type 0 with subtype string, tried to read BinaryObjectString but got "
                        + str(v)
                        + " instead of 6 @ "
                        + hex(i.tell() - 1)
                    )
                i.read(4)  # skip id
                pos = i.tell()
                values.append((_read_str(i), pos))
            else:
                pos = i.tell()
                values.append((prim_readers[info - 1](i), pos))
        elif x == 2:
            r = i.read(1)[0]
            if r != 8:
                raise ValueError(
                    "Got object type 2, tried to read MemberPrimitiveTyped but got "
                    + str(r)
                    + " instead of 8 @ "
                    + hex(i.tell() - 1)
                )
            pos = i.tell()
            values.append((prim_readers[i.read(1)](i), pos))
        elif x == 5 or x == 6 or x == 7:
            v = i.read(1)[0]
            if v != 9:
                raise ValueError(
                    "Got object type 5, 6 or 7 (array), tried to read MemberReference but got "
                    + str(v)
                    + " instead of 9 @ "
                    + hex(i.tell() - 1)
                )
            pos = i.tell()
            idr = struct.unpack("<i", i.read(4))[0]
            values.append((idr, pos))
    end = _pos_of(i, -1)

    with indented(f"ClassWithMembersAndTypes [yellow]({start}, {end})[/yellow]"):
        with indented(f"ClassInfo [yellow]({start_ci}, {end_ci})[/yellow]"):
            print_prop("object_id", cls_info.object_id)
            print_prop("name", cls_info.name)
        fancy_print(f"MemberTypeInfo [yellow]({start_mti}, {end_mti})[/yellow]")
        fancy_print(f"[green]fields[/green] = [")
        ind()
        for (a, b, name, value) in zip(
            member_type_info.bin_type_enums,
            member_type_info.additional_infos,
            cls_info.members,
            values,
        ):
            if a == 0:  # primitive
                type_name = type_names[b - 1]
                fancy_print(
                    f"[magenta]{type_name}[/magenta] [green]{name}[/green]"
                    f" = [blue]{value[0]}[/blue]  (value defined at [yellow]{hex(value[1])[:]}[/yellow])"
                )
            elif a == 7:
                fancy_print(
                    f"[magenta]{type_names[b-1]}[][/magenta] [green]{name}[/green]"
                    f" = [blue]<Ref to array [yellow]{value[0]}[/yellow]>[/blue]  (value reference defined at [yellow]{hex(value[1])[:]}[/yellow])"
                )
            else:
                fancy_print(
                    f"[magenta]{bin_type_enums[a]}[/magenta] [green]{name}[/green]"
                    f" = [blue]{value[0]}[/blue]  (value defined at [yellow]{hex(value[1])[:]}[/yellow])"
                    f" ; additional info: {b}"
                )
        ded()
        fancy_print("]")
        # p(f"values = [")
        # ind()
        # for x in values:
        #     p(str(x))
        # ded()
        # p("]")
        print_prop("library_id", lib_id)


def _decode_binary_library(i: IO):
    start = _pos_of(i, -1)
    lib_id = struct.unpack("<i", i.read(4))[0]
    lib_name = _read_str(i)
    end = _pos_of(i, -1)
    with indented(f"BinaryLibrary [yellow]({start}, {end})[/yellow]"):
        print_prop("library_id", lib_id)
        print_prop("library_name", lib_name)


def _decode_prim_array(i: IO):
    start = _pos_of(i, -1)
    oid = struct.unpack("<i", i.read(4))[0]
    length_def = _pos_of(i)
    length = struct.unpack("<i", i.read(4))[0]
    tpe = i.read(1)[0]
    reader = prim_readers[tpe - 1]
    els = [(i.tell(), reader(i)) for _ in range(length)]
    end = _pos_of(i, -1)
    with indented(f"PrimitiveArray [yellow]({start}, {end})[/yellow]"):
        print_prop("id", oid)
        fancy_print(
            f"[green]length[/green] = [blue]{length}[/blue]  (defined at [yellow]{length_def}[/yellow])"
        )
        if length > 0:
            fancy_print(f"[green]elements[/green] [")
            ind()
            for x in els:
                fancy_print(
                    f"[blue]{x[1]}[/blue]  (defined at [yellow]{hex(x[0])}[/yellow])"
                )
            ded()
            fancy_print("]")
        else:
            fancy_print(f"[green]elements[/green] \\[]")


deserializers = {
    0: _decode_ser_header_record,
    1: _decode_class_with_id,
    5: _decode_cls_with_members_and_types,
    12: _decode_binary_library,
    15: _decode_prim_array,
    11: lambda i: fancy_print("-- End --"),
}


def decode(i: IO):
    while True:  # check if data is left
        buf = i.read(1)
        if len(buf) == 0:
            break  # EOF
        current_id = buf[0]
        if current_id not in deserializers:
            print(
                f"! Tried to deserialize record with ID {current_id}; deserializer not found. At: "
                + hex(i.tell())
            )
            sys.exit(1)
        deserializers[current_id](i)


def main():
    if len(sys.argv) < 2:
        print("syntax: decode_bepis.py file.bepis")
        sys.exit(1)
    f = Path(sys.argv[1])
    if not f.is_file():
        print(f"! {f} is not a file")
        sys.exit(1)
    with open(f, "rb") as file:
        decode(file)


if __name__ == "__main__":
    main()
from pathlib import Path

from components import Client

if __name__ == "__main__":
    client = Client(
        name="in_code_client",
        broker_protocol_path=Path("protocol/broker.avpr").absolute(),
        protocol_path=Path("protocol/client.avpr").absolute(),
        broker_socket="/tmp/broker.sock",
        component_socket="/tmp/client.sock",
    )

    try:
        print(f"create_table received : {client.create_table('test')}")
    except Exception as e:
        print(e)

    try:
        docids = client.insert(
            "test",
            [
                {"name": "luc", "age": 27, "height": 1.79, "hobby": None},
                {},
                {"name": "te'st", "age": 99},
            ],
        )
        print(f"insert received : {docids}")
    except Exception as e:
        print(e)

    try:
        print(f"select received : {client.select('test', ['name', 'age'], None)}")
    except Exception as e:
        print(e)

    try:
        print(f"select where received : {client.select('test', ['name', 'age'], 'age = 99')}")
    except Exception as e:
        print(e)

    try:
        print(f"update received : {client.update('test', ('age', 100))}")
    except Exception as e:
        print(e)

    try:
        print(f"select after update received : {client.select('test', ['name', 'age'], None)}")
    except Exception as e:
        print(e)

    try:
        luc_filter = "name = 'luc'"
        print(f"delete received : {client.delete('test', luc_filter)}")
    except Exception as e:
        print(e)

    try:
        print(f"select after delete received : {client.select('test', ['name'], None)}")
    except Exception as e:
        print(e)

    try:
        print(f"delete all received : {client.delete('test', None)}")
    except Exception as e:
        print(e)

    try:
        print(f"insert into missing table received : {client.insert('ghost', [{'a': 1}])}")
    except Exception as e:
        print(e)

    try:
        print(f"select from missing table received : {client.select('ghost', ['a'], None)}")
    except Exception as e:
        print(e)

    try:
        print(f"update missing table received : {client.update('ghost', ('a', 1))}")
    except Exception as e:
        print(e)

    try:
        print(f"select broken filter received : {client.select('test', ['a'], 'age =')}")
    except Exception as e:
        print(e)

    try:
        print(f"insert bool received : {client.insert('test', [{'ok': True}])}")
    except Exception as e:
        print(e)

    try:
        print(f"delete_table received : {client.delete_table('test')}")
    except Exception as e:
        print(e)

    try:
        print(f"delete_table again received : {client.delete_table('test')}")
    except Exception as e:
        print(e)

import asyncio
import autobahn.asyncio.websocket
import collections
import enums
import random
import sys
import traceback
import ujson


class AcquireServerProtocol(autobahn.asyncio.websocket.WebSocketServerProtocol):
    next_client_id = 1
    client_id_to_client = {}
    client_ids = set()
    usernames = set()
    next_game_id = 1
    game_id_to_game = {}
    client_ids_and_messages = []

    def __init__(self):
        self.username = ''
        self.client_id = None
        self.game_id = None
        self.player_id = None

    def onConnect(self, request):
        self.username = ' '.join(request.params.get('username', [''])[0].split())
        print('X', 'connect', self.peer, self.username)
        print()

    def onOpen(self):
        self.client_id = AcquireServerProtocol.next_client_id
        AcquireServerProtocol.next_client_id += 1
        AcquireServerProtocol.client_id_to_client[self.client_id] = self
        AcquireServerProtocol.client_ids.add(self.client_id)
        messages_client = [[enums.CommandsToClient.SetClientId.value, self.client_id]]

        print(self.client_id, 'open', self.peer)

        if len(self.username) == 0 or len(self.username) > 32:
            messages_client.append([enums.CommandsToClient.FatalError.value, enums.FatalErrors.InvalidUsername.value])
            AcquireServerProtocol.add_pending_messages({self.client_id}, messages_client)
            self.flush_pending_messages()
            self.sendClose()
        elif self.username in AcquireServerProtocol.usernames:
            messages_client.append([enums.CommandsToClient.FatalError.value, enums.FatalErrors.UsernameAlreadyInUse.value])
            AcquireServerProtocol.add_pending_messages({self.client_id}, messages_client)
            self.flush_pending_messages()
            self.sendClose()
        else:
            AcquireServerProtocol.usernames.add(self.username)

            # tell client about other clients' data
            enum_set_client_id_to_data = enums.CommandsToClient.SetClientIdToData.value
            for client in AcquireServerProtocol.client_id_to_client.values():
                if client is not self:
                    messages_client.append([enum_set_client_id_to_data, client.client_id, client.username, client.peer])
            AcquireServerProtocol.add_pending_messages({self.client_id}, messages_client)
            messages_client = []

            # tell all clients about client's data
            AcquireServerProtocol.add_pending_messages(AcquireServerProtocol.client_ids, [[enum_set_client_id_to_data, self.client_id, self.username, self.peer]])

            # tell client about all games
            set_game_state = enums.CommandsToClient.SetGameState.value
            client_index = enums.ScoreSheetIndexes.Client.value
            username_index = enums.ScoreSheetIndexes.Username.value
            set_game_player_username = enums.CommandsToClient.SetGamePlayerUsername.value
            set_game_player_client_id = enums.CommandsToClient.SetGamePlayerClientId.value
            set_game_watcher_client_id = enums.CommandsToClient.SetGameWatcherClientId.value
            for game_id, game in AcquireServerProtocol.game_id_to_game.items():
                messages_client.append([set_game_state, game_id, game.state])
                for player_id, player_datum in enumerate(game.score_sheet.player_data):
                    if player_datum[client_index] is None:
                        messages_client.append([set_game_player_username, game_id, player_id, player_datum[username_index]])
                    else:
                        messages_client.append([set_game_player_client_id, game_id, player_id, player_datum[client_index].client_id])
                for client_id in game.watcher_client_ids:
                    messages_client.append([set_game_watcher_client_id, game_id, client_id])
            AcquireServerProtocol.add_pending_messages({self.client_id}, messages_client)

            self.flush_pending_messages()

    def onClose(self, wasClean, code, reason):
        print(self.client_id, 'close')

        if self.game_id is not None:
            AcquireServerProtocol.game_id_to_game[self.game_id].remove_client(self)

        AcquireServerProtocol.add_pending_messages(AcquireServerProtocol.client_ids, [[enums.CommandsToClient.SetClientIdToData.value, self.client_id, None, None]])
        self.flush_pending_messages()

        del AcquireServerProtocol.client_id_to_client[self.client_id]
        AcquireServerProtocol.client_ids.discard(self.client_id)
        AcquireServerProtocol.usernames.discard(self.username)

    def onMessage(self, payload, isBinary):
        if not isBinary:
            try:
                message = payload.decode()
                print(self.client_id, '->', message)
                message = ujson.decode(message)
                method = getattr(self, 'onMessage' + enums.CommandsToServer(message[0]).name)
                arguments = message[1:]
            except:
                traceback.print_exc()
                self.sendClose()
                return

            try:
                method(*arguments)
                self.flush_pending_messages()
            except TypeError:
                traceback.print_exc()
                self.sendClose()
        else:
            self.sendClose()

    def onMessageCreateGame(self):
        if self.game_id is None:
            AcquireServerProtocol.game_id_to_game[AcquireServerProtocol.next_game_id] = Game(AcquireServerProtocol.next_game_id, self)
            AcquireServerProtocol.next_game_id += 1

    def onMessageJoinGame(self, game_id):
        if self.game_id is None and game_id in AcquireServerProtocol.game_id_to_game:
            AcquireServerProtocol.game_id_to_game[game_id].join_game(self)

    def onMessageRejoinGame(self, game_id):
        if self.game_id is None and game_id in AcquireServerProtocol.game_id_to_game:
            AcquireServerProtocol.game_id_to_game[game_id].rejoin_game(self)

    def onMessageWatchGame(self, game_id):
        if self.game_id is None and game_id in AcquireServerProtocol.game_id_to_game:
            AcquireServerProtocol.game_id_to_game[game_id].watch_game(self)

    def onMessageLeaveGame(self):
        if self.game_id is not None:
            AcquireServerProtocol.game_id_to_game[self.game_id].remove_client(self)

    def onMessageDoGameAction(self, game_action_id, *data):
        if self.game_id is not None:
            AcquireServerProtocol.game_id_to_game[self.game_id].do_game_action(self, game_action_id, data)

    @staticmethod
    def add_pending_messages(client_ids, messages):
        client_ids = client_ids.copy()
        new_list = []
        for client_ids2, messages2 in AcquireServerProtocol.client_ids_and_messages:
            client_ids_in_group = client_ids2 & client_ids
            if len(client_ids_in_group) == len(client_ids2):
                messages2.extend(messages)
                new_list.append([client_ids2, messages2])
            elif len(client_ids_in_group) > 0:
                new_list.append([client_ids_in_group, messages2 + messages])
                client_ids2 -= client_ids_in_group
                new_list.append([client_ids2, messages2])
            else:
                new_list.append([client_ids2, messages2])
            client_ids -= client_ids_in_group
        if len(client_ids) > 0:
            new_list.append([client_ids, messages])
        AcquireServerProtocol.client_ids_and_messages = new_list

    @staticmethod
    def flush_pending_messages():
        for client_ids, messages in AcquireServerProtocol.client_ids_and_messages:
            messages_json = ujson.dumps(messages)
            print(','.join(str(x) for x in sorted(client_ids)), '<-', messages_json)
            messages_json_bytes = messages_json.encode()
            for client_id in client_ids:
                AcquireServerProtocol.client_id_to_client[client_id].sendMessage(messages_json_bytes)
        del AcquireServerProtocol.client_ids_and_messages[:]
        print()


class GameBoard:
    def __init__(self, client_ids):
        self.client_ids = client_ids

        nothing = enums.GameBoardTypes.Nothing.value
        self.x_to_y_to_board_type = [[nothing for y in range(9)] for x in range(12)]
        self.board_type_to_coordinates = collections.defaultdict(set)
        self.board_type_to_coordinates[nothing].update((x, y) for x in range(12) for y in range(9))

    def set_cell(self, coordinates, board_type):
        x, y = coordinates
        old_board_type = self.x_to_y_to_board_type[x][y]
        self.board_type_to_coordinates[old_board_type].remove(coordinates)
        self.x_to_y_to_board_type[x][y] = board_type
        self.board_type_to_coordinates[board_type].add(coordinates)
        AcquireServerProtocol.add_pending_messages(self.client_ids, [[enums.CommandsToClient.SetGameBoardCell.value, x, y, board_type]])


class ScoreSheet:
    def __init__(self, game_id):
        self.game_id = game_id

        self.player_data = []
        self.available = [25, 25, 25, 25, 25, 25, 25]
        self.chain_size = [0, 0, 0, 0, 0, 0, 0]
        self.price = [0, 0, 0, 0, 0, 0, 0]

    def add_player(self, client, starting_tile):
        messages_all = []
        messages_client = []

        self.player_data.append([0, 0, 0, 0, 0, 0, 0, 60, 60, client.username, starting_tile, client, len(self.player_data) == 0])
        self.player_data.sort(key=lambda x: x[enums.ScoreSheetIndexes.StartingTile.value])

        username_index = enums.ScoreSheetIndexes.Username.value
        starting_tile_index = enums.ScoreSheetIndexes.StartingTile.value
        client_index = enums.ScoreSheetIndexes.Client.value
        nothing_yet = enums.GameBoardTypes.NothingYet.value
        set_game_player_username = enums.CommandsToClient.SetGamePlayerUsername.value
        set_game_player_client_id = enums.CommandsToClient.SetGamePlayerClientId.value
        set_game_board_cell = enums.CommandsToClient.SetGameBoardCell.value

        # update player_ids for all clients in game
        player_id = 0
        for player_datum in self.player_data:
            if player_datum[client_index] is not None:
                player_datum[client_index].player_id = player_id
            player_id += 1

        for player_id, player_datum in enumerate(self.player_data):
            # tell everybody about player changes
            player_datum = self.player_data[player_id]
            if player_id >= client.player_id:
                if player_datum[client_index] is None:
                    messages_all.append([set_game_player_username, self.game_id, player_id, player_datum[username_index]])
                else:
                    messages_all.append([set_game_player_client_id, self.game_id, player_id, player_datum[client_index].client_id])

            # tell client about other starting tiles
            if player_id != client.player_id:
                starting_tile = player_datum[starting_tile_index]
                messages_client.append([set_game_board_cell, starting_tile[0], starting_tile[1], nothing_yet])

        AcquireServerProtocol.add_pending_messages(AcquireServerProtocol.client_ids, messages_all)
        if len(messages_client) > 0:
            AcquireServerProtocol.add_pending_messages({client.client_id}, messages_client)

    def readd_player(self, client):
        messages_all = []

        username_index = enums.ScoreSheetIndexes.Username.value
        client_index = enums.ScoreSheetIndexes.Client.value
        set_game_player_client_id = enums.CommandsToClient.SetGamePlayerClientId.value

        for player_id, player_datum in enumerate(self.player_data):
            if client.username == player_datum[username_index]:
                client.player_id = player_id
                player_datum[client_index] = client
                messages_all.append([set_game_player_client_id, self.game_id, player_id, client.client_id])

        AcquireServerProtocol.add_pending_messages(AcquireServerProtocol.client_ids, messages_all)

    def remove_client(self, client):
        messages_all = []

        client_index = enums.ScoreSheetIndexes.Client.value
        set_game_player_client_id = enums.CommandsToClient.SetGamePlayerClientId.value

        for player_id, player_datum in enumerate(self.player_data):
            if client is player_datum[client_index]:
                player_datum[client_index].player_id = None
                player_datum[client_index] = None
                messages_all.append([set_game_player_client_id, self.game_id, player_id, None])

        AcquireServerProtocol.add_pending_messages(AcquireServerProtocol.client_ids, messages_all)

    def is_username_in_game(self, username):
        username_index = enums.ScoreSheetIndexes.Username.value

        for player_datum in self.player_data:
            if username == player_datum[username_index]:
                return True
        return False

    def get_creator_player_id(self):
        is_creator_index = enums.ScoreSheetIndexes.IsCreator.value
        for player_id, player_datum in enumerate(self.player_data):
            if player_datum[is_creator_index]:
                return player_id

    def get_player_id_to_client_id(self):
        player_id_to_client_id = []

        client_index = enums.ScoreSheetIndexes.Client.value

        for player_datum in self.player_data:
            client = player_datum[client_index]
            player_id_to_client_id.append(None if client is None else client.client_id)

        return player_id_to_client_id


class TileBag:
    def __init__(self):
        tiles = [(x, y) for x in range(12) for y in range(9)]
        random.shuffle(tiles)
        self.tiles = tiles

    def get_tile(self):
        if len(self.tiles) > 0:
            return self.tiles.pop()
        else:
            return None

    def __len__(self):
        return len(self.tiles)


class Action:
    command_id = enums.CommandsToClient.SetGameAction.value

    def __init__(self, game, player_id, game_action_id):
        self.game = game
        self.player_id = player_id
        self.game_action_id = game_action_id
        self.player_params = []
        self.other_params = []

    def prepare(self):
        pass

    def send_message(self, client=None):
        target_client_ids = self.game.client_ids if client is None else {client.client_id}
        message = [Action.command_id, self.game_action_id, self.player_id]

        if len(self.player_params) == 0 and len(self.other_params) == 0:
            AcquireServerProtocol.add_pending_messages(target_client_ids, [message])
        else:
            player_client_ids = target_client_ids & {self.game.player_id_to_client_id[self.player_id]}
            other_client_ids = target_client_ids - player_client_ids

            if len(player_client_ids) > 0:
                AcquireServerProtocol.add_pending_messages(player_client_ids, [message + self.player_params])
            if len(other_client_ids) > 0:
                AcquireServerProtocol.add_pending_messages(other_client_ids, [message + self.other_params])


class ActionStartGame(Action):
    def __init__(self, game, player_id):
        super().__init__(game, player_id, enums.GameActions.StartGame.value)

    def execute(self):
        AcquireServerProtocol.add_pending_messages(self.game.client_ids, [[enums.CommandsToClient.AddGameHistoryMessage.value, enums.GameHistoryMessages.StartedGame.value, self.player_id]])

        self.game.state = enums.GameStates.InProgress.value
        AcquireServerProtocol.add_pending_messages(AcquireServerProtocol.client_ids, [[enums.CommandsToClient.SetGameState.value, self.game.game_id, self.game.state]])

        self.game.tile_racks.draw_initial_tiles()

        return [ActionPlayTile(self.game, 0), ActionPurchaseStock(self.game, 0)]


class ActionPlayTile(Action):
    def __init__(self, game, player_id):
        super().__init__(game, player_id, enums.GameActions.PlayTile.value)

    def execute(self, tile_index):
        if not isinstance(tile_index, int):
            return
        tile_data = self.game.tile_racks.racks[self.player_id]
        if tile_index < 0 or tile_index >= len(tile_data):
            return
        tile_datum = tile_data[tile_index]
        if tile_datum is None:
            return

        tile = tile_datum[0]

        tile_data[tile_index] = None
        self.game.game_board.set_cell(tile, enums.GameBoardTypes.NothingYet.value)

        message = [enums.CommandsToClient.AddGameHistoryMessage.value, enums.GameHistoryMessages.PlayedTile.value, self.player_id, tile[0], tile[1]]
        AcquireServerProtocol.add_pending_messages(self.game.client_ids, [message])

        return True


class ActionPurchaseStock(Action):
    def __init__(self, game, player_id):
        super().__init__(game, player_id, enums.GameActions.PurchaseStock.value)

    def prepare(self):
        self.game.tile_racks.draw_tile(self.player_id)

        next_player_id = (self.player_id + 1) % len(self.game.player_id_to_client_id)
        return [ActionPlayTile(self.game, next_player_id), ActionPurchaseStock(self.game, next_player_id)]

    def execute(self):
        pass


class TileRacks:
    def __init__(self, game):
        self.game = game
        self.racks = []
        self._e_add_game_history_message = enums.CommandsToClient.AddGameHistoryMessage.value
        self._e_drew_tile = enums.GameHistoryMessages.DrewTile.value
        self._e_set_tile = enums.CommandsToClient.SetTile.value

    def draw_initial_tiles(self):
        for player_id in range(len(self.game.player_id_to_client_id)):
            self.racks.append([None] * 6)
            self.draw_tile(player_id)

    def draw_tile(self, player_id):
        will_put_lonely_tile_down = enums.GameBoardTypes.WillPutLonelyTileDown.value

        tile_data = self.racks[player_id]
        messages = []

        for tile_index, tile_datum in enumerate(tile_data):
            if tile_datum is None:
                tile = self.game.tile_bag.get_tile()
                if tile is not None:
                    game_board_type_id = will_put_lonely_tile_down
                    tile_data[tile_index] = [tile, game_board_type_id]

                    messages.append([self._e_add_game_history_message, self._e_drew_tile, player_id, tile[0], tile[1]])
                    messages.append([self._e_set_tile, tile_index, tile[0], tile[1], game_board_type_id])

        client_id = self.game.player_id_to_client_id[player_id]
        if client_id is not None:
            AcquireServerProtocol.add_pending_messages({client_id}, messages)


class Game:
    def __init__(self, game_id, client):
        self.game_id = game_id
        self.client_ids = set()
        self.watcher_client_ids = set()

        self.game_board = GameBoard(self.client_ids)
        self.score_sheet = ScoreSheet(game_id)
        self.tile_bag = TileBag()
        self.tile_racks = TileRacks(self)

        self.state = enums.GameStates.Starting.value
        self.actions = collections.deque()

        AcquireServerProtocol.add_pending_messages(AcquireServerProtocol.client_ids, [[enums.CommandsToClient.SetGameState.value, self.game_id, self.state]])

        self.client_ids.add(client.client_id)
        client.game_id = self.game_id
        starting_tile = self.tile_bag.get_tile()
        self.game_board.set_cell(starting_tile, enums.GameBoardTypes.NothingYet.value)
        self.score_sheet.add_player(client, starting_tile)
        self.player_id_to_client_id = self.score_sheet.get_player_id_to_client_id()
        message = [enums.CommandsToClient.AddGameHistoryMessage.value, enums.GameHistoryMessages.DrewStartingTile.value, client.player_id, starting_tile[0], starting_tile[1]]
        AcquireServerProtocol.add_pending_messages(self.client_ids, [message])
        self.actions.append(ActionStartGame(self, self.score_sheet.get_creator_player_id()))
        self.actions[0].send_message()

    def join_game(self, client):
        if self.state == enums.GameStates.Starting.value and not self.score_sheet.is_username_in_game(client.username):
            self.client_ids.add(client.client_id)
            client.game_id = self.game_id
            starting_tile = self.tile_bag.get_tile()
            self.game_board.set_cell(starting_tile, enums.GameBoardTypes.NothingYet.value)
            previous_creator_player_id = self.score_sheet.get_creator_player_id()
            self.score_sheet.add_player(client, starting_tile)
            self.player_id_to_client_id = self.score_sheet.get_player_id_to_client_id()
            message = [enums.CommandsToClient.AddGameHistoryMessage.value, enums.GameHistoryMessages.DrewStartingTile.value, client.player_id, starting_tile[0], starting_tile[1]]
            AcquireServerProtocol.add_pending_messages(self.client_ids, [message])
            creator_player_id = self.score_sheet.get_creator_player_id()
            if creator_player_id != previous_creator_player_id:
                self.actions.clear()
                self.actions.append(ActionStartGame(self, creator_player_id))
                self.actions[0].send_message()
            else:
                self.actions[0].send_message(client)

    def rejoin_game(self, client):
        if self.score_sheet.is_username_in_game(client.username):
            self.client_ids.add(client.client_id)
            client.game_id = self.game_id
            self.score_sheet.readd_player(client)
            self.player_id_to_client_id = self.score_sheet.get_player_id_to_client_id()
            self.send_initialization_messages(client)

    def watch_game(self, client):
        if not self.score_sheet.is_username_in_game(client.username):
            self.client_ids.add(client.client_id)
            self.watcher_client_ids.add(client.client_id)
            client.game_id = self.game_id
            AcquireServerProtocol.add_pending_messages(AcquireServerProtocol.client_ids, [[enums.CommandsToClient.SetGameWatcherClientId.value, self.game_id, client.client_id]])
            self.send_initialization_messages(client)

    def remove_client(self, client):
        if client.client_id in self.client_ids:
            client.game_id = None
            if client.client_id in self.watcher_client_ids:
                self.watcher_client_ids.discard(client.client_id)
                AcquireServerProtocol.add_pending_messages(AcquireServerProtocol.client_ids, [[enums.CommandsToClient.ReturnWatcherToLobby.value, self.game_id, client.client_id]])
            self.score_sheet.remove_client(client)
            self.player_id_to_client_id = self.score_sheet.get_player_id_to_client_id()
            self.client_ids.discard(client.client_id)

    def do_game_action(self, client, game_action_id, data):
        action = self.actions[0]
        if client.player_id is not None and client.player_id == action.player_id and game_action_id == action.game_action_id:
            new_actions = action.execute(*data)
            while new_actions:
                self.actions.popleft()
                if isinstance(new_actions, list):
                    new_actions.reverse()
                    self.actions.extendleft(new_actions)
                action = self.actions[0]
                new_actions = action.prepare()
            action.send_message()

    def send_initialization_messages(self, client):
        # game board
        messages = [[enums.CommandsToClient.SetGameBoard.value, self.game_board.x_to_y_to_board_type]]

        # score sheet
        net_index = enums.ScoreSheetIndexes.Net.value
        score_sheet_data = [
            [x[:net_index + 1] for x in self.score_sheet.player_data],
            self.score_sheet.available,
            self.score_sheet.chain_size,
            self.score_sheet.price,
        ]
        messages.append([enums.CommandsToClient.SetScoreSheet.value, score_sheet_data])

        # player's tiles
        if client.player_id is not None:
            set_tile = enums.CommandsToClient.SetTile.value
            for tile_index, tile_datum in enumerate(self.tile_racks.racks[client.player_id]):
                if tile_datum is not None:
                    tile = tile_datum[0]
                    messages.append([set_tile, tile_index, tile[0], tile[1], tile_datum[1]])

        AcquireServerProtocol.add_pending_messages({client.client_id}, messages)

        # action
        self.actions[0].send_message(client)


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'debug':
        debug = True
    else:
        debug = False

    factory = autobahn.asyncio.websocket.WebSocketServerFactory('ws://127.0.0.1:9000', debug=debug)
    factory.protocol = AcquireServerProtocol

    loop = asyncio.get_event_loop()
    coro = loop.create_server(factory, '127.0.0.1', 9000)
    server = loop.run_until_complete(coro)

    try:
        loop.run_forever()
    except KeyboardInterrupt:
        pass
    except:
        traceback.print_exc()
    finally:
        server.close()
        loop.close()


import asyncio
import threading
import time
from typing import Optional, Callable
from javascript import require, On, Once, AsyncTask, off

# Require mineflayer and plugins
mineflayer = require('mineflayer')
pathfinder = require('mineflayer-pathfinder')
collectblock = require('mineflayer-collectblock')
pvp = require('mineflayer-pvp').plugin
autoeat = require('mineflayer-auto-eat').plugin
tool = require('mineflayer-tool').plugin
armorManager = require('mineflayer-armor-manager')

class MinecraftBot:
    """
    Контроллер Minecraft бота использующий mineflayer (Node.js) через python-javascript bridge.
    """
    def __init__(self, bot_name: str = "VoiceBot"):
        self.bot_name = bot_name
        self.bot = None
        self.host = "localhost"
        self.port = 25565
        self.version = False # Автоопределение
        self.is_running = False
        self._loop = asyncio.get_event_loop()
        self.on_chat_callback: Optional[Callable[[str, str], None]] = None # (username, message) -> None

    def connect(self, host: str, port: int = 25565, username: Optional[str] = None):
        """Подключает бота к серверу Minecraft."""
        if self.is_running:
            print("Бот уже запущен.")
            return

        self.host = host
        self.port = port
        if username:
            self.bot_name = username

        print(f"Подключение к {self.host}:{self.port} как {self.bot_name}...")
        
        # Создаем инстанс бота
        self.bot = mineflayer.createBot({
            "host": self.host,
            "port": self.port,
            "username": self.bot_name,
            "auth": "offline", 
            "version": False
        })

        # Загружаем плагины
        self.bot.loadPlugin(pathfinder.pathfinder)
        self.bot.loadPlugin(collectblock.plugin)
        self.bot.loadPlugin(pvp)
        self.bot.loadPlugin(autoeat)
        self.bot.loadPlugin(tool)
        self.bot.loadPlugin(armorManager)

        self.is_running = True
        self._setup_events()

    def disconnect(self):
        """Отключает бота."""
        if self.bot:
            self.bot.quit()
            self.bot = None
        self.is_running = False
        print("Бот отключен.")

    def _setup_events(self):
        """Настраивает события mineflayer."""
        
        @On(self.bot, 'spawn')
        def on_spawn(this, *args):
            print("Бот заспавнился!")
            mcData = require('minecraft-data')(self.bot.version)
            self.movements = pathfinder.Movements(self.bot, mcData)
            self.bot.pathfinder.setMovements(self.movements)
            # Auto eat setup
            self.bot.autoEat.options.priority = "foodPoints"
            self.bot.autoEat.options.startAt = 14
            self.bot.autoEat.options.bannedFood = []

        @On(self.bot, 'chat')
        def on_chat(this, username, message, *args):
            if username == self.bot_name:
                return
            print(f"[MC] {username}: {message}")
            if self.on_chat_callback:
                pass 

        @On(self.bot, 'error')
        def on_error(this, err, *args):
            print(f"Ошибка бота: {err}")

        @On(self.bot, 'kicked')
        def on_kicked(this, reason, *args):
            print(f"Бот кикнут: {reason}")
            self.is_running = False
        
        @On(self.bot, 'autoeat_started')
        def on_autoeat_started(this, item, offhand, *args):
            print(f"[MC] Кушаю {item['name']}...")

        @On(self.bot, 'autoeat_stopped')
        def on_autoeat_stopped(this, *args):
            print("[MC] Перестал кушать.")
            
        @On(self.bot, 'health')
        def on_health(this, *args):
            if self.bot.health < 10:
                print(f"[MC] Warning: Low health ({int(self.bot.health)})")

    def chat(self, message: str):
        """Отправляет сообщение в чат."""
        if self.bot:
            self.bot.chat(message)

    def move_to(self, x: float, y: float, z: float):
        """Идёт к координатам."""
        if not self.bot: 
            return
            
        goal = pathfinder.goals.GoalBlock(x, y, z)
        self.bot.pathfinder.setGoal(goal)

    def follow_player(self, player_name: str):
        """Слеудет за игроком."""
        if not self.bot:
            return
            
        target = self.bot.players[player_name]
        if not target or not target.entity:
            print(f"Игрок {player_name} не найден или не виден.")
            return

        goal = pathfinder.goals.GoalFollow(target.entity, 1)
        self.bot.pathfinder.setGoal(goal, True) 

    def stop_moving(self):
        """Останавливает все действия (движение, копание, атаку)."""
        if self.bot:
            self.bot.pathfinder.setGoal(None)
            self.bot.pvp.stop()
            # Stop mining/collecting if possible (API differs, usually stop pathfinder helps)
            print("Бот остановлен.")

    def mine_block(self, block_name: str, count: int = 1):
        """Ищет и добывает блок."""
        if not self.bot: return

        mcData = require('minecraft-data')(self.bot.version)
        block_type = mcData.blocksByName[block_name]
        
        if not block_type:
            print(f"Неизвестный блок: {block_name}")
            self.chat(f"Я не знаю блок {block_name}")
            return

        print(f"Ищу и копаю {count} {block_name}...")
        
        # Используем collectBlock плагин
        try:
            # find blocks
            blocks = self.bot.findBlocks({
                "matching": block_type.id,
                "maxDistance": 64,
                "count": count
            })
            
            if not blocks or len(blocks) == 0:
                self.chat(f"Не вижу {block_name} поблизости.")
                return
            
            # collect via collectblock plugin
            # Note: python-javascript converts JS arrays/objects automatically
            # We need to construct callback or usage appropriately. 
            # The simple usage for collectblock is bot.collectBlock.collect(targets, cb)
            
            targets = []
            for vec in blocks:
                targets.append(self.bot.blockAt(vec))
                
            self.bot.collectBlock.collect(targets, callback=lambda err: print("Добыча завершена" if not err else f"Ошибка добычи: {err}"))
            
        except Exception as e:
            print(f"Ошибка при добыче: {e}")

    def attack_entity(self, entity_name: str):
        """Атакует ближайшую сущность данного типа."""
        if not self.bot: return
        
        # Найти сущность
        target = self.bot.nearestEntity(lambda e: e.name == entity_name) # simplified filter
        # In JS: bot.nearestEntity(e => e.name === 'zombie')
        # In Python wrapper we might need to pass a lambda that effectively works or iterate entities safely.
        # Let's try to iterate entities manually to find best match if lambda fails across bridge or complex.
        
        # Better: iterate self.bot.entities
        best_target = None
        min_dist = 999
        
        # bot.entities is a dict of id -> entity
        for ent_id in self.bot.entities:
            ent = self.bot.entities[ent_id]
            # check type
            if ent.name == entity_name or (entity_name == "player" and ent.type == "player"):
                # calc dist
                if ent.position:
                    dist = self.bot.entity.position.distanceTo(ent.position)
                    if dist < min_dist and dist < 30: # 30 blocks radius
                        min_dist = dist
                        best_target = ent

        if best_target:
            print(f"Атакую {best_target.name} (dist: {int(min_dist)})")
            self.bot.pvp.attack(best_target)
        else:
            self.chat(f"Не вижу {entity_name} поблизости.")

    def equip_item(self, item_name: str, destination: str = "hand"):
        """Экипирует предмет."""
        if not self.bot: return
        
        # destination: hand, head, torso, legs, feet
        mcData = require('minecraft-data')(self.bot.version)
        item_id = mcData.itemsByName[item_name]
        
        if not item_id:
             print(f"Unknown item {item_name}")
             return

        try:
            item = self.bot.inventory.findInventoryItem(item_id.id, None)
            if item:
                self.bot.equip(item, destination)
                print(f"Экипировал {item_name}")
            else:
                self.chat(f"У меня нет {item_name}")
        except Exception as e:
            print(f"Ошибка экипировки: {e}")

    def get_inventory(self) -> str:
        """Возвращает список предметов в инвентаре."""
        if not self.bot: return "Инвентарь недоступен"
        
        items = self.bot.inventory.items()
        if not items:
            return "Инвентарь пуст."
        
        # items is list of Item objects
        # We aggregate counts
        counts = {}
        for item in items:
            name = item.name
            count = item.count
            counts[name] = counts.get(name, 0) + count
            
        report = []
        for name, count in counts.items():
            report.append(f"{name}: {count}")
            
        return ", ".join(report)

    def get_status_info(self) -> str:
        """Возвращает сводку статуса бота."""
        if not self.bot or not self.is_running:
            return "Бот офлайн."
        
        if not self.bot.entity:
             return "Бот подключается..."

        pos = self.bot.entity.position
        health = self.bot.health
        food = self.bot.food
        
        inv = self.get_inventory()
        if len(inv) > 50: inv = inv[:50] + "..."
        
        return (
            f"Бот '{self.bot.username}' онлайн.\n"
            f"Позиция: {int(pos.x)}, {int(pos.y)}, {int(pos.z)}\n"
            f"HP: {int(health)}/20, Еда: {int(food)}/20\n"
            f"Инвентарь: {inv}"
        )

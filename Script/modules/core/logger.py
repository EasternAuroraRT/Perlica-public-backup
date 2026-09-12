import logging
import copy
from colorama import Fore, Style, Back, init

log_level = logging.DEBUG
# log_level = logging.INFO

class UserRestart(Exception):
    pass

class ColoredFormatter(logging.Formatter):
    def __init__(self, fmt=None, datefmt=None,
                 prefix_color_map=None,
                 message_color_map=None):
        """
        :param fmt: 日志格式，必须包含 %(message)s
        :param datefmt: 时间格式
        :param prefix_color_map: dict，键为级别名（如 'INFO'），值为 colorama 颜色代码
                                 用于着色除 %(message)s 以外的所有内容（前缀+后缀）
        :param message_color_map: dict，键为级别名，值为 colorama 颜色代码
                                  用于着色 %(message)s 本身
        """
        super().__init__(fmt, datefmt)
        self.prefix_color_map = prefix_color_map or {
            'DEBUG': Back.CYAN + Style.BRIGHT + Fore.BLACK,
            'INFO': Style.BRIGHT + Fore.LIGHTBLACK_EX,
            'WARNING': Style.BRIGHT + Fore.YELLOW,
            'ERROR': Style.BRIGHT + Fore.MAGENTA,
            'CRITICAL': Back.RED + Style.BRIGHT + Fore.BLACK,
        }
        self.message_color_map = message_color_map or {
            'DEBUG': Fore.LIGHTCYAN_EX,
            'INFO': Fore.WHITE,
            'WARNING': Fore.LIGHTYELLOW_EX,
            'ERROR': Fore.LIGHTMAGENTA_EX,
            'CRITICAL': Style.BRIGHT + Fore.RED,
        }

    @staticmethod
    def _colorize_lines(text, color):
        """为文本的每一行独立着色（避免换行后颜色丢失）"""
        if not text or not color:
            return text
        lines = text.split('\n')
        colored = [color + line + Style.RESET_ALL for line in lines]
        return '\n'.join(colored)

    def format(self, record):
        # 1. 构造临时记录，将消息置空，得到格式化后的“前缀+后缀”部分
        temp_record = copy.copy(record)
        temp_record.msg = ''
        temp_record.args = None
        prefix_part = super().format(temp_record)   # 如 "[2026-...] [INFO]\n"

        # 2. 获取原始消息
        message = record.getMessage()

        # 3. 获取对应级别的颜色
        level = record.levelname
        prefix_color = self.prefix_color_map.get(level, '')
        msg_color = self.message_color_map.get(level, '')

        # 4. 分别按行着色
        colored_prefix = self._colorize_lines(prefix_part, prefix_color)
        colored_message = self._colorize_lines(message, msg_color)

        # 5. 拼接
        return colored_prefix + colored_message

# log = logging.getLogger()
log = logging.getLogger('log')
log.setLevel(log_level)

# 添加控制台输出（StreamHandler）
console_handler = logging.StreamHandler()
# console_formatter = logging.Formatter('\033[90m[%(asctime)s] [%(levelname)s]\033[0m\n%(message)s')
console_formatter = ColoredFormatter('[%(asctime)s] [%(levelname)s]\n%(message)s')
console_handler.setFormatter(console_formatter)
log.addHandler(console_handler)

if __name__ == "__main__":
    log.debug('hello')
    log.info('hello')
    log.warning('hello')
    log.error('hello')
    log.critical('hello')
import __builtin__ as python
import Queue as queue
import threading
import copy
import logging
import importlib


# Local imports

import internal.parser
import lang


# Global variables

global_interpreter_lock = threading.Lock()


def __isiter(object):

    try:

        iter(object)

        return True

    except TypeError, e:

        return False


def __run(expression, globals_, locals_, return_value_queue, iterate_literal_arrays):

    threads = []

    (operator, atom) = expression[0]

    ignore_iterables = [str, unicode, dict]

    if not iterate_literal_arrays:

        ignore_iterables = ignore_iterables + [list, tuple]

    # TODO: This is a hack to support instances, should be re-written. If atom is `literal` or `instance`, it should appear as a metadata/attribute

    try:

        object_or_objects = python.eval(atom, globals_, locals_)

    except NameError, e:

        try:

            module_full_name = atom

            # Find the longest possible module name

            while True:

                module_full_name = module_full_name[:module_full_name.rindex('.')]

                # EXIT #1: EOS, thus rindex will raise ValueError if module_full_name does not contain '.'

                try:

                    importlib.import_module(module_full_name)

                except ImportError, e:

                    # Try a shorter module name

                    continue

                # EXIT #2: module_full_name is a valid import()-able name

                break

            # Load modules in revrse (bottom to top) order

            prefix = ""

            for module_name in module_full_name.split('.'):

                globals_.update({prefix + module_name: importlib.import_module(prefix + module_name)})

                prefix = prefix + module_name + '.'

            # Try again

            object_or_objects = python.eval(atom, globals_, locals_)

        except Exception, e1:

            # raise NameError

            raise e

    except TypeError, e:

        # Due to eval()?

        if (e.message == 'eval() arg 1 must be a string or code object'):

            object_or_objects = atom

        else:

            raise e

    # [1,2,3] -> [a,b,c] =
    #       Thread 1: 1 -> [a,b,c]
    #       Thread 2: 2 -> [a,b,c]
    #       ...

    if not isinstance(object_or_objects, tuple(ignore_iterables)) and __isiter(object_or_objects):

        for item in object_or_objects:

            # TODO: This is a hack to prevent from item to be confused as fcn call and raise NameError.

            if isinstance(item, basestring):

                # i.e. [1, 'Hello'] -> eval() = [1, Hello] , this fixup Hello to be 'Hello' again

                item = "'" + item + "'"

            thread = threading.Thread(target=__run, args=([(operator, item)] + expression[1:], copy.copy(globals_), copy.copy(locals_), return_value_queue, not iterate_literal_arrays))

            thread.start()

            # Synchronous

            if operator == '|':

                thread.join()

            # Asynchronous

            else:

                threads.append(thread)

        # Asynchronous

        if threads:

            # Wait for threads

            for thread in threads:

                thread.join(None)

    # 1 -> [a,b,c]

    else:

        # Get current input

        input = locals_.get('_', None)

        if input is None:

            input = globals_.get('_', None)

        ####################################################
        # `output` = `input` applied on `object_or_objects`#
        ####################################################

        # Assume `object_or_objects` is literal (i.e. object_or_objects override input)

        output = object_or_objects

        # Instance? (e.g. function, instance of class that implements __call__)

        if callable(output):

            # Remote?

            if isinstance(output, lang.remotefunction):

                output.evaluate_host(globals_, locals_)

            # Python Statement?

            if isinstance(output, (lang.stmt, lang.expr)):

                output = output(globals_, locals_)

            else:

                output = output(input)

            # Reset `ignore_iterables` if callable(), thus allowing a function return value to be iterated

            ignore_iterables = [str, unicode, dict]

        # Special Values

        if output is False:

            # 1 -> False = <Terminate Thread>

            return None

        if output is True:

            # 1 -> True = 1

            output = input

        if output is None:

            # 1 -> None = 1

            output = input

        if isinstance(output, dict):

            # 1 -> {1: 'One', 2: 'Two'} = 'One'

            output = output.get(input, False)

            if output is False:

                return None

        # `output` is array or discrete?

        if not isinstance(output, tuple(ignore_iterables)) and __isiter(output):

            # Iterate `output`

            for item in output:

                globals_['_'] = locals_['_'] = item

                if expression[1:]:

                    # Call next atom in expression with `item` as `input`

                    thread = threading.Thread(target=__run, args=(expression[1:], copy.copy(globals_), copy.copy(locals_), return_value_queue, True))

                    thread.start()

                    threads.append(thread)

                else:

                    return_value_queue.put((item, globals_, locals_))

        else:

            # Same thread, next atom

            globals_['_'] = locals_['_'] = output

            if expression[1:]:

                # Call next atom in expression with `output` as `input`

                __run(expression[1:], copy.copy(globals_), copy.copy(locals_), return_value_queue, True)

            else:

                return_value_queue.put((output, globals_, locals_))

        for thread in threads:

            thread.join(None)


def __extend_builtins(globals_):

    # TODO: Is there any advantage to manually duplicate __builtins__, instead of passing our own?

    globals_['__builtins__'] = python

    # Add `pythonect.lang` to Python's `__builtins__`

    for name in dir(lang):

        # i.e. __builtins__.print_ = pythonect.lang.print_

        setattr(globals_['__builtins__'], name, getattr(lang, name))

    # Add GIL

    setattr(globals_['__builtins__'], '__GIL__', global_interpreter_lock)

    return globals_


def __merge_dicts(d1, d2, ignore_keys):

    result = dict(d1)

    for k, v in d2.iteritems():

        if k not in ignore_keys:

            if k in result:

                if result[k] != v:

                    # TODO: Is this the best way to handle multiple/different v`s of k?

                    del result[k]

                    ignore_keys.update({k: True})

            else:

                result[k] = v

    return result


def __merge_all_globals_and_locals(current_globals, current_locals, globals_list=[], ignore_globals_keys={}, locals_list=[], ignore_locals_keys={}):

    current_globals = __merge_dicts(current_globals, globals_list.pop(), ignore_globals_keys)

    current_locals = __merge_dicts(current_locals, locals_list.pop(), ignore_locals_keys)

    if not globals_list or not locals_list:

        return current_globals, current_locals

    return __merge_all_globals_and_locals(current_globals, current_locals, globals_list, ignore_globals_keys, locals_list, ignore_locals_keys)


def eval(source, globals_, locals_):

    return_value = None

    # Meaningful program?

    if source != "pass":

        return_values = []

        globals_values = []

        locals_values = []

        waiting_list = []

        # Parse Pythonect

        parser = internal.parser.Parser()

        # Extend Python's __builtin__ with Pythonect's `lang`

        final_globals_ = __extend_builtins(globals_)

        # Default input

        if final_globals_.get('_', None) is None:

            final_globals_['_'] = locals_.get('_', None)

        # Iterate Pythonect program

        for expression in parser.parse(source):

            # Execute Pythonect expression

            thread_return_value_queue = queue.Queue()

            thread = threading.Thread(target=__run, args=(expression, final_globals_, locals_, thread_return_value_queue, True))

            thread.start()

            waiting_list.append((thread, thread_return_value_queue))

        # Join threads by execution order

        for (thread, thread_queue) in waiting_list:

            thread.join()

            try:

                # While queue contain return value(s)

                while True:

                    (thread_return_value, thread_globals, thread_locals) = thread_queue.get(True, 1)

                    thread_queue.task_done()

                    return_values.append(thread_return_value)

                    locals_values.append(thread_locals)

                    globals_values.append(thread_globals)

            except queue.Empty:

                pass

        return_value = return_values

        # [...] ?

        if return_value:

            # Single return value? (e.g. [1])

            if len(return_value) == 1:

                return_value = return_value[0]

            # Update globals_ and locals_

            new_globals, new_locals = __merge_all_globals_and_locals(globals_, locals_, globals_values, {}, locals_values, {})

            globals_.update(new_globals)

            locals_.update(new_locals)

        # [] ?

        else:

            return_value = False

        # Set `return value` as `_`

        globals_['_'] = locals_['_'] = return_value

    return return_value

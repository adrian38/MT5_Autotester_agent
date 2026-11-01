# El hilo del nodo muere solo, y el puerto se queda abierto

## Lo que pasó el 2026-11-01

A las 04:35:54 el manager avisó de que no podía consultar RoboForex ECN:
`<urlopen error [Errno 111] Connection refused>`. El `Errno 111` es de Linux
porque quien pregunta es el contenedor del manager; desde Windows la misma
negativa sale como `[WinError 10061]`.

El agente **no estaba caído ni colgado**. Su proceso (`app_ui.py`, arrancado a
la 1:05:49) terminó el backtest `manual_history_20261101_041949` a las 04:35:56
con `return_code: 0` y escribió su `state.json`. Lo único que dejó de funcionar
fue el bucle `accept()` del nodo, hacia las 04:20.

La huella en el socket, nueve horas después:

```
State     RemoteAddress  RemotePort  CreationTime
Listen    0.0.0.0                 0  01/11/2026 1:07:23
CloseWait 192.168.1.152       62824  01/11/2026 4:21:36
... cinco más, todas entre las 4:21:36 y las 4:21:38
```

Seis conexiones de dos segundos y ninguna después, cuando el manager sondea
cada cinco minutos: en nueve horas tendrían que ser cientos. El bucle dejó de
aceptar, esas seis se quedaron en la cola de escucha —`socketserver` la fija en
**5** con `request_queue_size`—, el manager las abandonó al agotar su
`timeout=10` y pasaron a `CLOSE_WAIT` sin que nadie las recogiera. Con la cola
llena, Windows contesta RST a todo lo que llegue después.

De ahí viene lo que más despista: **el síntoma final es «conexión rechazada»,
pero el fallo original es que nadie acepta**. Los primeros sondeos fueron
timeouts; sólo cuando se llenó la cola empezaron los rechazos. Un rechazo es
indistinguible de un agente apagado, así que el aviso del manager apunta al
sitio equivocado.

## Por qué no se pudo saber la causa

`serve_forever` sólo se sale por `shutdown()` o por una excepción que no
atrapa. Lo primero lo hace `stop()`, y `logs/manager_node.log` lo habría
anotado: su última línea es «Nodo integrado iniciado» de la 1:07:24 y no hay
ninguna de «detenido». Lo segundo mata el hilo e imprime la traza por
`threading.excepthook`, que escribe en `stderr`. Con `run_ui.bat` (`py
app_ui.py`) `stderr` es la consola del lanzador, y ese `cmd` ya no existía.

Descartado por el camino, para no volver a mirarlo:

- `status()` no se bloquea: `_read_status_snapshot` usa `acquire(blocking=False)`
  sobre un snapshot publicado aparte.
- `database_snapshot()` abre la memoria en sólo lectura con `timeout=2`.
- `NodeServer` es `ThreadingHTTPServer` con `daemon_threads`, así que un
  manejador lento no frena el `accept()`.

## Lo que se añadió

- **`crash_log.install(BASE_DIR)`**, lo primero de `app_ui.main()`. Duplica
  `stderr` en `logs/app_stderr.log` y engancha los dos `excepthook`
  conservando los anteriores. La próxima traza estará en disco.
- **`EmbeddedManagerNode.supervise()`**, llamada desde el bucle de Tk cada
  `NODE_SUPERVISION_INTERVAL_MS` (5 s). Si el hilo murió, cierra el socket
  huérfano —lo que además suelta las conexiones a medio cerrar— y levanta otro
  servidor **sobre el mismo `JobController`**: uno nuevo sobre el mismo
  `state.json` serían dos escritores para la generación en curso.

`stop()` deja `address` en `None`, así que una parada deliberada no se
deshace. Un relanzamiento que falla —el puerto puede tardar en soltarse— se
reintenta en la vuelta siguiente y se anota una sola vez, no cada cinco
segundos.

## Lo que sigue sin cubrirse

`supervise()` mira `thread.is_alive()`. Si el hilo quedara **vivo pero
bloqueado** dentro del bucle, no lo vería. Las pruebas de ese día no permiten
descartarlo del todo, aunque un bloqueo de nueve horas dentro de
`_handle_request_noblock` no tiene explicación. El día que vuelva a pasar, la
diferencia se lee en los logs: con hilo muerto hay traza en `app_stderr.log` y
línea de relanzamiento en `manager_node.log`; con hilo vivo y atascado, los dos
ficheros se quedan mudos. Entonces tocaría una sonda real contra
`/api/v1/health` en lugar de mirar la bandera.

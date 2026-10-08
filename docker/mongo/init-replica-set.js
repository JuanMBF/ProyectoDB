// Inicializa el replica set "rs0" con tres miembros. mongo1 tiene más prioridad: mientras esté sano es el
// primario, así desde fuera de Docker se puede escribir conectándose directo al puerto 27017.
// Es idempotente: si el replica set ya está configurado, solo muestra su estado.
const config = {
  _id: 'rs0',
  members: [
    { _id: 0, host: 'mongo1:27017', priority: 2 },
    { _id: 1, host: 'mongo2:27017' },
    { _id: 2, host: 'mongo3:27017' },
  ],
};

try {
  rs.status();
  print('El replica set ya estaba inicializado.');
} catch (error) {
  if (error.codeName !== 'NotYetInitialized') throw error;
  rs.initiate(config);
  print('Replica set rs0 inicializado.');
}

// Espera a que haya un primario elegido antes de terminar.
for (let i = 0; i < 60; i++) {
  const primary = rs.status().members.find((m) => m.stateStr === 'PRIMARY');
  if (primary) {
    print(`Primario: ${primary.name}`);
    quit(0);
  }
  sleep(1000);
}
print('No se eligió un primario a tiempo.');
quit(1);

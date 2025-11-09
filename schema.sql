-- Schema SQL para Supabase
-- Tabla de registros procesados desde imágenes OCR

-- Crear la tabla registros
CREATE TABLE IF NOT EXISTS registros (
    registro_id BIGSERIAL PRIMARY KEY,
    id TEXT NOT NULL,
    folio TEXT NOT NULL,
    hora TEXT NOT NULL,
    estado TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Crear índice para búsquedas por folio
CREATE INDEX IF NOT EXISTS idx_registros_folio ON registros(folio);

-- Crear índice para búsquedas por estado
CREATE INDEX IF NOT EXISTS idx_registros_estado ON registros(estado);

-- Crear índice para ordenar por hora
CREATE INDEX IF NOT EXISTS idx_registros_hora ON registros(hora);

-- Crear índice para ordenar por fecha de creación
CREATE INDEX IF NOT EXISTS idx_registros_created_at ON registros(created_at);

-- Agregar restricción CHECK para validar el formato de hora (HH:MM)
ALTER TABLE registros 
ADD CONSTRAINT check_hora_format 
CHECK (hora ~ '^([0-1][0-9]|2[0-3]):[0-5][0-9]$');

-- Agregar restricción CHECK para validar los valores de estado
ALTER TABLE registros 
ADD CONSTRAINT check_estado_valido 
CHECK (estado IN ('completado', 'pendiente', 'indefinido'));

-- Comentarios en las columnas (opcional, para documentación)
COMMENT ON TABLE registros IS 'Registros procesados desde imágenes de tablas mediante OCR';
COMMENT ON COLUMN registros.registro_id IS 'ID único autoincremental del registro';
COMMENT ON COLUMN registros.id IS 'Identificador del registro extraído de la imagen (generalmente una letra)';
COMMENT ON COLUMN registros.folio IS 'Número de folio completo';
COMMENT ON COLUMN registros.hora IS 'Hora en formato HH:MM';
COMMENT ON COLUMN registros.estado IS 'Estado del registro: completado, pendiente o indefinido';
COMMENT ON COLUMN registros.created_at IS 'Fecha y hora de creación del registro';


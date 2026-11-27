select * from archivio_comunale.file_originali

select * from archivio_comunale.documenti

select * from archivio_comunale.ordinanze

select * from archivio_comunale.determinazioni

select det.numero_determinazione, d.categoria, d.data_atto, d.oggetto, det.ufficio_responsabile,det.firmatario, fo.nome_originale, fo.acquisito_il from archivio_comunale.documenti as d 
join archivio_comunale.file_originali as fo on d.id = fo.documento_id 
join archivio_comunale.determinazioni as det on fo.documento_id = det.documento_id
where d.id = '6'